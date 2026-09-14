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
# Task 1. Create custom mode VPC networks
#         with firewall rules
# -----------------------------------------------

# Create managementnet
module "managementnet" {
  source  = "terraform-google-modules/network/google"
  version = "18.2.0"

  network_name = "managementnet"
  project_id   = var.project_id

  subnets = [
    {
      subnet_name   = "managementsubnet-us"
      subnet_region = var.compute_region
      subnet_ip     = "10.130.0.0/20"
    },
  ]
}

# Create privatenet
module "privatenet" {
  source  = "terraform-google-modules/network/google"
  version = "18.2.0"

  network_name = "privatenet"
  project_id   = var.project_id

  subnets = [
    {
      subnet_name   = "privatesubnet-us"
      subnet_region = var.compute_region
      subnet_ip     = "172.16.0.0/24"
    },
    {
      subnet_name   = "privatesubnet-notus"
      subnet_region = var.compute_region
      subnet_ip     = "172.20.0.0/20"
    },
  ]
}

# Create the firewall rules for managementnet
resource "google_compute_firewall" "mnet-allow-icmp-ssh-rdp" {
  name    = "managementnet-allow-icmp-ssh-rdp"
  network = module.managementnet.network_self_link

  allow {
    protocol = "icmp"
  }

  allow {
    protocol = "tcp"
    ports    = [22, 3389]
  }

  source_ranges = ["0.0.0.0/0"]
}

# Create the firewall rules for privatenet
resource "google_compute_firewall" "pnet-allow-icmp-ssh-rdp" {
  name    = "privatenet-allow-icmp-ssh-rdp"
  network = module.privatenet.network_self_link

  allow {
    protocol = "icmp"
  }

  allow {
    protocol = "tcp"
    ports    = [22, 3389]
  }

  source_ranges = ["0.0.0.0/0"]
}

# -----------------------------------------------
# Task 2. Create VM instances
# -----------------------------------------------

resource "google_compute_instance" "mnet-us-vm" {
  name         = "managementnet-us-vm"
  machine_type = "e2-medium"
  zone         = var.compute_zone

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
    }
  }

  network_interface {
    network    = module.managementnet.network_self_link
    subnetwork = "managementsubnet-us"

    access_config {
    }
  }
}

resource "google_compute_instance" "pnet-us-vm" {
  name         = "privatenet-us-vm"
  machine_type = "e2-medium"
  zone         = var.compute_zone

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
    }
  }

  network_interface {
    network    = module.privatenet.network_self_link
    subnetwork = "privatesubnet-us"

    access_config {
    }
  }
}

# -----------------------------------------------
# Task 4. Create a VM instance with multiple 
#         network interfaces
# -----------------------------------------------

resource "google_compute_instance" "vm-appliance" {
  name         = "vm-appliance"
  machine_type = "e2-standard-4"
  zone         = var.compute_zone

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
    }
  }

  network_interface {
    network    = module.privatenet.network_self_link
    subnetwork = "privatesubnet-us"
  }

  network_interface {
    network    = module.managementnet.network_self_link
    subnetwork = "managementsubnet-us"
  }

  network_interface {
    network    = "mynetwork"
    subnetwork = "mynetwork"
  }
}

