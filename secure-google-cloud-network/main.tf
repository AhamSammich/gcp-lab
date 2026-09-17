terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "8.2.0"
    }
  }

  backend "local" {
    path = "./terraform.tfstate"
  }
}

provider "google" {
  # Configuration options
}

data "google_compute_subnetwork" "acme_mgmt" {
  name   = "acme-mgmt-subnet"
  region = var.compute_region
  project = var.project_id
}

module "firewall_rules" {
  source  = "terraform-google-modules/network/google//modules/firewall-rules"
  version = "18.3.0"

  project_id   = var.project_id
  network_name = data.google_compute_subnetwork.acme_mgmt.network

  ingress_rules = [
    {
      name = "allow-ssh-iap-ingress-to-bastion"
      allow = [
        {
          protocol      = "tcp"
          ports         = [22]
        },
      ]
      target_tags   = [var.ssh-iap-tag]
      source_ranges = ["35.235.240.0/20"]
      log_config = {
        metadata = "INCLUDE_ALL_METADATA"
      }
    },
    {
      name = "allow-http-ingress-to-juice-shop"
      allow = [
        {
          protocol      = "tcp"
          ports         = [80]
        },
      ]
      target_tags   = [var.http-tag]
      source_ranges = ["0.0.0.0/0"]
      log_config = {
        metadata = "INCLUDE_ALL_METADATA"
      }
    },
    {
      name = "allow-ssh-ingress-from-acme-mgmt-subnet"
      allow = [
        {
          protocol      = "tcp"
          ports         = [22]
        },
      ]
      target_tags   = [var.ssh-internal-tag]
      source_ranges = [data.google_compute_subnetwork.acme_mgmt.ip_cidr_range]
      log_config = {
        metadata = "INCLUDE_ALL_METADATA"
      }
    },
  ]
}

