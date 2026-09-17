terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "7.46.1"
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
# Task 1. Enable APIs
# -----------------------------------------------

locals {
  gcp_services = [
    "servicenetworking.googleapis.com",
    "ids.googleapis.com",
    "logging.googleapis.com",
  ]
}

resource "google_project_service" "enabled_apis" {
  for_each = toset(local.gcp_services)

  project = var.project_id
  service = each.key
  disable_on_destroy = false
}

# -----------------------------------------------
# Task 2. Build the Google Cloud networking footprint
# -----------------------------------------------

module "ids_vpc" {
  source = "terraform-google-modules/network/google"
  version = "18.3.0"

  network_name = "cloud-ids"
  project_id = var.project_id

  subnets = [
    {
      subnet_name = "cloud-ids-useast1"
      subnet_region = "us-east1"
      subnet_ip = "192.168.10.0/24"
    },
  ]
}

resource "google_compute_global_address" "ids_ips" {
  name = "cloud-ids-ips"
  address = "10.10.10.0"
  address_type = "INTERNAL"
  purpose = "VPC_PEERING"
  prefix_length = 24
  description = "Cloud IDS Range"
  network = module.ids_vpc.network_name
}

resource "google_service_networking_connection" "ids_connect" {
  network                 = module.ids_vpc.network_name
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.ids_ips.name]
}

# -----------------------------------------------
# Task 3. Create a Cloud IDS endpoint
# -----------------------------------------------

resource "google_cloud_ids_endpoint" "ids_endpoint" {
    name     = "cloud-ids-east1"
    location = "us-east1-b"
    network  = module.ids_vpc.network_name
    severity = "INFORMATIONAL"
    depends_on = [google_service_networking_connection.ids_connect]
}

# -----------------------------------------------
# Task 4. Create Firewall rules and Cloud NAT
# -----------------------------------------------

module "ids_firewall" {
  source = "terraform-google-modules/network/google//modules/firewall-rules"
  version = "18.3.0"

  project_id = var.project_id
  network_name = module.ids_vpc.network_name

  ingress_rules = [
    {
      name = "allow-http-icmp"
      source_ranges = ["0.0.0.0/0"]
      allow = [
        {
          protocol = "tcp"
          ports = [80]
        },
        {
          protocol = "icmp"
        },
      ]
      priority = 1000
    },
    {
      name = "allow-iap-proxy"
      source_ranges = ["35.235.240.0/20"]
      allow = [
        {
          protocol = "tcp"
          ports = [22]
        },
      ]
      priority = 1000
    },
  ]
}

module "cloud-nat" {
  source  = "terraform-google-modules/cloud-nat/google"
  version = "7.0.0"

  project_id = var.project_id
  region = "us-east1"
  router = "cr-cloud-ids-useast1"
  create_router = true
  network = module.ids_vpc.network_name
}

# -----------------------------------------------
# Task 5. Create two virtual machines
# -----------------------------------------------

# Simple Debian web-server
resource "google_compute_instance" "server" {
  name = "server"
  zone = "us-east1-b"
  machine_type = "e2-medium"
  tags = ["server"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size = 10
    }
  }

  network_interface {
    subnetwork = module.ids_vpc.subnets["us-east1/cloud-ids-useast1"].name
    network_ip="192.168.10.20"
  }

  metadata_startup_script = <<-EOT
  #!/bin/bash
  sudo apt-get update
  sudo apt-get -qq -y install nginx
  EOT
}

# Simulated attacker client
resource "google_compute_instance" "attacker" {
  name = "attacker"
  zone = "us-east1-b"
  machine_type = "e2-medium"

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size = 10
    }
  }

  network_interface {
    subnetwork = module.ids_vpc.subnets["us-east1/cloud-ids-useast1"].name
    network_ip="192.168.10.10"
  }
}

# -----------------------------------------------
# Task 6. Create a Cloud IDS packet mirroring policy
# -----------------------------------------------

resource "google_compute_packet_mirroring" "ids_mirror" {
  name = "cloud-ids-packet-mirroring"
  region = "us-east1"
  enable = "TRUE"
  
  network {
    url = module.ids_vpc.network_self_link
  }
  
  collector_ilb {
    url = google_cloud_ids_endpoint.ids_endpoint.endpoint_forwarding_rule
  }

  mirrored_resources {
    subnetworks {
      url = module.ids_vpc.subnets["us-east1/cloud-ids-useast1"].self_link
    }
  }
}

# -----------------------------------------------
# Task 9. Manual Incident Response
# -----------------------------------------------

# resource "google_compute_firewall" "emergency_block" {
#   name = "cymbal-emergency-block"
#   network = module.ids_vpc.network_name
#   direction = "INGRESS"
#   priority = 1
#   source_ranges = ["${google_compute_instance.attacker.network_interface.0.network_ip}/32"]
#
#   deny {
#     protocol = "all"
#   }
#
#   log_config {
#     metadata = "INCLUDE_ALL_METADATA"
#   }
# }
