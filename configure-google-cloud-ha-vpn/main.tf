terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "6.50.0"
    }
  }

  backend "local" {
    path = "./terraform.tfstate"
  }
}

provider "google" {
  # Configuration options
}

# -----------------------------------------------
# Task 1. Set up a Global VPC environment
# -----------------------------------------------

module "vpc-demo" {
  source  = "terraform-google-modules/network/google"
  version = "18.2.0"

  project_id   = var.project_id
  network_name = "vpc-demo"
  # Module default is GLOBAL, but lab expects default REGIONAL
  routing_mode = "REGIONAL"

  subnets = [
    {
      subnet_name   = "vpc-demo-subnet1"
      subnet_ip     = "10.1.1.0/24"
      subnet_region = var.region1
    },
    {
      subnet_name   = "vpc-demo-subnet2"
      subnet_ip     = "10.2.1.0/24"
      subnet_region = var.region2
    }
  ]
}

resource "google_compute_firewall" "vpc-demo-allow-ssh-icmp" {
  name    = "vpc-demo-allow-ssh-icmp"
  network = module.vpc-demo.network_self_link

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  allow {
    protocol = "icmp"
  }

  source_ranges = ["0.0.0.0/0"]
}

# Create two VM instances in separate regions/zones
resource "google_compute_instance" "vpc-demo-instance1" {
  name         = "vpc-demo-instance1"
  machine_type = "e2-medium"
  zone         = var.zone1

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
    }
  }

  network_interface {
    network    = module.vpc-demo.network_self_link
    subnetwork = module.vpc-demo.subnets["${var.region1}/vpc-demo-subnet1"].self_link
  }
}

resource "google_compute_instance" "vpc-demo-instance2" {
  name         = "vpc-demo-instance2"
  machine_type = "e2-medium"
  zone         = var.zone2

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
    }
  }

  network_interface {
    network    = module.vpc-demo.network_self_link
    subnetwork = module.vpc-demo.subnets["${var.region2}/vpc-demo-subnet2"].self_link
  }
}

# -----------------------------------------------
# Task 2. Set up a simulated on-premises environment
# -----------------------------------------------

module "on-prem" {
  source  = "terraform-google-modules/network/google"
  version = "18.2.0"

  project_id   = var.project_id
  network_name = "on-prem"

  subnets = [
    {
      subnet_name   = "on-prem-subnet1"
      subnet_ip     = "192.168.1.0/24"
      subnet_region = var.region1
    }
  ]
}

resource "google_compute_firewall" "on-prem-allow-custom" {
  name    = "on-prem-allow-custom"
  network = module.on-prem.network_self_link

  allow {
    protocol = "tcp"
    ports    = ["0-65535"]
  }

  allow {
    protocol = "udp"
    ports    = ["0-65535"]
  }

  allow {
    protocol = "icmp"
  }

  source_ranges = ["192.168.0.0/16"]
}

# NOTE: Not included in lab instructions. However, I was unable
#       to ssh from Cloud Shell (routed through IAP).
resource "google_compute_firewall" "on-prem-allow-iap-ssh" {
  name = "on-prem-allow-iap-ssh"
  network = module.on-prem.network_self_link

  allow {
    protocol = "tcp"
    ports = ["22"]
  }

  source_ranges = ["35.235.240.0/20"]
}

# Create a single remote instance
resource "google_compute_instance" "on-prem-instance1" {
  name         = "on-prem-instance1"
  machine_type = "e2-medium"
  zone         = var.zone1

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
    }
  }

  network_interface {
    network    = module.on-prem.network_self_link
    subnetwork = module.on-prem.subnets["${var.region1}/on-prem-subnet1"].self_link
  }
}

# -----------------------------------------------
# Task 3. Set up an HA VPN gateway
# -----------------------------------------------

resource "google_compute_ha_vpn_gateway" "vpc-demo-vpn-gw1" {
  name    = "vpc-demo-vpn-gw1"
  region  = var.region1
  network = module.vpc-demo.network_self_link
}

resource "google_compute_ha_vpn_gateway" "on-prem-vpn-gw1" {
  name    = "on-prem-vpn-gw1"
  region  = var.region1
  network = module.on-prem.network_self_link
}

resource "google_compute_router" "vpc-demo-router1" {
  name    = "vpc-demo-router1"
  network = module.vpc-demo.network_self_link
  region  = var.region1

  bgp {
    asn = 65001
  }
}

resource "google_compute_router" "on-prem-router1" {
  name    = "on-prem-router1"
  network = module.on-prem.network_self_link
  region  = var.region1

  bgp {
    asn = 65002
  }
}

# -----------------------------------------------
# Task 4. Create two VPN tunnels
# -----------------------------------------------

# Create pre-shared secret
resource "random_password" "shared_secret" {
  length = 32
  special = false
}

# Create tunnels
resource "google_compute_vpn_tunnel" "vpc-demo-tunnel0" {
  name                  = "vpc-demo-tunnel0"
  peer_gcp_gateway      = google_compute_ha_vpn_gateway.on-prem-vpn-gw1.self_link
  ike_version           = 2
  shared_secret         = random_password.shared_secret.result
  router                = google_compute_router.vpc-demo-router1.self_link
  vpn_gateway           = google_compute_ha_vpn_gateway.vpc-demo-vpn-gw1.self_link
  vpn_gateway_interface = 0
  region                = var.region1
}

resource "google_compute_vpn_tunnel" "vpc-demo-tunnel1" {
  name                  = "vpc-demo-tunnel1"
  peer_gcp_gateway      = google_compute_ha_vpn_gateway.on-prem-vpn-gw1.self_link
  ike_version           = 2
  shared_secret         = random_password.shared_secret.result
  router                = google_compute_router.vpc-demo-router1.self_link
  vpn_gateway           = google_compute_ha_vpn_gateway.vpc-demo-vpn-gw1.self_link
  vpn_gateway_interface = 1
  region                = var.region1
}

resource "google_compute_vpn_tunnel" "on-prem-tunnel0" {
  name                  = "on-prem-tunnel0"
  peer_gcp_gateway      = google_compute_ha_vpn_gateway.vpc-demo-vpn-gw1.self_link
  ike_version           = "2"
  shared_secret         = random_password.shared_secret.result
  router                = google_compute_router.on-prem-router1.self_link
  vpn_gateway           = google_compute_ha_vpn_gateway.on-prem-vpn-gw1.self_link
  vpn_gateway_interface = 0
  region                = var.region1
}

resource "google_compute_vpn_tunnel" "on-prem-tunnel1" {
  name                  = "on-prem-tunnel1"
  peer_gcp_gateway      = google_compute_ha_vpn_gateway.vpc-demo-vpn-gw1.self_link
  ike_version           = "2"
  shared_secret         = random_password.shared_secret.result
  router                = google_compute_router.on-prem-router1.self_link
  vpn_gateway           = google_compute_ha_vpn_gateway.on-prem-vpn-gw1.self_link
  vpn_gateway_interface = 1
  region                = var.region1
}

# -----------------------------------------------
# Task 5. Create Border Gateway Protocol (BGP) for each tunnel
# -----------------------------------------------

# Create router interface and BGP peer: 
# For `tunnel0` in network `vpc-demo`
resource "google_compute_router_interface" "if-tunnel0-to-on-prem" {
  name       = "if-tunnel0-to-on-prem"
  router     = google_compute_router.vpc-demo-router1.name
  ip_range   = "169.254.0.1/30"
  vpn_tunnel = google_compute_vpn_tunnel.vpc-demo-tunnel0.self_link
  region     = var.region1
}

resource "google_compute_router_peer" "bgp-on-prem-tunnel0" {
  name            = "bgp-on-prem-tunnel0"
  router          = google_compute_router.vpc-demo-router1.name
  interface       = google_compute_router_interface.if-tunnel0-to-on-prem.name
  peer_ip_address = "169.254.0.2"
  peer_asn        = 65002
  region          = var.region1
}

# For `tunnel1` in network `vpc-demo`
resource "google_compute_router_interface" "if-tunnel1-to-on-prem" {
  name       = "if-tunnel1-to-on-prem"
  router     = google_compute_router.vpc-demo-router1.name
  ip_range   = "169.254.1.1/30"
  vpn_tunnel = google_compute_vpn_tunnel.vpc-demo-tunnel1.self_link
  region     = var.region1
}

resource "google_compute_router_peer" "bgp-on-prem-tunnel1" {
  name            = "bgp-on-prem-tunnel1"
  router          = google_compute_router.vpc-demo-router1.name
  interface       = google_compute_router_interface.if-tunnel1-to-on-prem.name
  peer_ip_address = "169.254.1.2"
  peer_asn        = 65002
  region          = var.region1
}

# For `tunnel0` in network `on-prem`
resource "google_compute_router_interface" "if-tunnel0-to-vpc-demo" {
  name       = "if-tunnel0-to-vpc-demo"
  router     = google_compute_router.on-prem-router1.name
  ip_range   = "169.254.0.2/30"
  vpn_tunnel = google_compute_vpn_tunnel.on-prem-tunnel0.self_link
  region     = var.region1
}

resource "google_compute_router_peer" "bgp-vpc-demo-tunnel0" {
  name            = "bgp-vpc-demo-tunnel0"
  router          = google_compute_router.on-prem-router1.name
  interface       = google_compute_router_interface.if-tunnel0-to-vpc-demo.name
  peer_ip_address = "169.254.0.1"
  peer_asn        = 65001
  region          = var.region1
}

# For `tunnel1` in network `on-prem`
resource "google_compute_router_interface" "if-tunnel1-to-vpc-demo" {
  name       = "if-tunnel1-to-vpc-demo"
  router     = google_compute_router.on-prem-router1.name
  ip_range   = "169.254.1.2/30"
  vpn_tunnel = google_compute_vpn_tunnel.on-prem-tunnel1.self_link
  region     = var.region1
}

resource "google_compute_router_peer" "bgp-vpc-demo-tunnel1" {
  name            = "bgp-vpc-demo-tunnel1"
  router          = google_compute_router.on-prem-router1.name
  interface       = google_compute_router_interface.if-tunnel1-to-vpc-demo.name
  peer_ip_address = "169.254.1.1"
  peer_asn        = 65001
  region          = var.region1
}

# -----------------------------------------------
# Task 6. Verify router configurations
# -----------------------------------------------

# Configure firewall rules to allow traffic from the remote VPC
resource "google_compute_firewall" "vpc-demo-allow-subnets-from-on-prem" {
  name    = "vpc-demo-allow-subnets-from-on-prem"
  network = module.vpc-demo.network_self_link

  allow {
    protocol = "tcp"
  }

  allow {
    protocol = "udp"
  }

  allow {
    protocol = "icmp"
  }

  source_ranges = ["192.168.1.0/24"]
}

resource "google_compute_firewall" "on-prem-allow-subnets-from-vpc-demo" {
  name    = "on-prem-allow-subnets-from-vpc-demo"
  network = module.on-prem.network_self_link

  allow {
    protocol = "tcp"
  }

  allow {
    protocol = "udp"
  }

  allow {
    protocol = "icmp"
  }

  source_ranges = ["10.1.1.0/24", "10.2.1.0/24"]
}

