terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "8.4.0"
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
# Task 1. Create the VM instance
# -----------------------------------------------

# Create the VPC network
module "vpc_privatenet" {
  source  = "terraform-google-modules/network/google"
  version = "18.3.0"

  network_name = "privatenet"
  project_id = var.project_id

  subnets = [
    {
      subnet_name = "privatenet-us"
      subnet_region = var.region
      subnet_ip = "10.130.0.0/20"
      # subnet_private_access = true # Uncomment and apply for Task 2
    },
  ]
}

# Create the firewall rule
resource "google_compute_firewall" "allow_ssh_iap" {
  name = "privatenet-allow-ssh"
  network = module.vpc_privatenet.network_name

  allow {
    protocol = "tcp"
    ports = [22]
  }

  source_ranges = ["35.235.240.0/20"] # IAP range
}

# Create the VM instance with no public IP address
resource "google_compute_instance" "vm_internal" {
  name = "vm-internal"
  zone = var.zone
  machine_type = "e2-standard-2"

  boot_disk {
    initialize_params {
      image = "debian-12"
    }
  }

  network_interface {
    network = module.vpc_privatenet.network_name
    subnetwork = module.vpc_privatenet.subnets_names[0]

    # Omitted `access_config` block to ensure no external IP
  }
}

# -----------------------------------------------
# Task 2. Enable Private Google Access
# -----------------------------------------------

resource "google_storage_bucket" "bucket" {
  name = "${var.project_id}-bucket"
  location = "US"
  uniform_bucket_level_access = true
  public_access_prevention = "enforced"
}

# Uncomment `subnet_private_access = true` above and apply

# -----------------------------------------------
# Task 3. Configure a Cloud NAT Gateway
# -----------------------------------------------

# module "cloud-nat" { # Uncomment and apply module
#
#   source  = "terraform-google-modules/cloud-nat/google"
#   version = "7.1.0"
#
#   project_id = var.project_id
#   region = var.region
#   router = "nat-router"
#
#   name = "nat-config"
#   network = module.vpc_privatenet.network_name
#   create_router = true
#
#
#   # # -----------------------------------------------
#   # # Task 4. Configure and view logs with Cloud NAT Logging
#   # # -----------------------------------------------
#   #
#   # log_config_enable = true
#   # log_config_filter = "ALL"
#
# }

