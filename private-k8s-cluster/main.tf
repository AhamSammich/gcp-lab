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

# -----------------------------------------------
# Task 2. Creating a private cluster 
# -----------------------------------------------

resource "google_container_cluster" "private_cluster" {
  name            = "private-cluster"
  location        = var.zone
  subnetwork      = ""
  networking_mode = "VPC_NATIVE"

  private_cluster_config {
    enable_private_nodes   = true
    master_ipv4_cidr_block = "172.16.0.16/28"
  }

  master_authorized_networks_config {
    cidr_blocks {
      cidr_block   = "${google_compute_instance.src_vm.network_interface.0.access_config.0.nat_ip}/32"
      display_name = google_compute_instance.src_vm.name
    }
  }

  initial_node_count = 1

  node_config {
    machine_type = "e2-medium"
  }

  deletion_protection = false
}

# -----------------------------------------------
# Task 4. Enable master authorized networks
# -----------------------------------------------

resource "google_compute_instance" "src_vm" {
  name         = "source-instance"
  zone         = var.zone
  machine_type = "e2-medium"

  boot_disk {
    initialize_params {
      image = "debian-12"
    }
  }

  network_interface {
    network = "default"
    access_config {}
  }

  service_account {
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  allow_stopping_for_update = true
  metadata_startup_script   = <<-EOT
  #!/bin/bash
  sudo apt-get update
  sudo apt-get -y install kubectl
  sudo apt-get -y install google-cloud-cli-gke-gcloud-auth-plugin
  EOT
}

# -----------------------------------------------
# Task 6. Create a private cluster that uses a custom subnetwork
# -----------------------------------------------

# resource "google_compute_subnetwork" "mysubnet" {
#   name                     = "my-subnet"
#   network                  = "default"
#   ip_cidr_range            = "10.0.4.0/22"
#   private_ip_google_access = true
#   region                   = var.region
#
#   secondary_ip_range {
#     range_name    = "my-svc-range"
#     ip_cidr_range = "10.0.32.0/20"
#   }
#
#   secondary_ip_range {
#     range_name    = "my-pod-range"
#     ip_cidr_range = "10.4.0.0/14"
#   }
# }
#
# resource "google_container_cluster" "private_cluster2" {
#   name            = "private-cluster2"
#   location        = var.zone
#   subnetwork      = google_compute_subnetwork.mysubnet.name
#   networking_mode = "VPC_NATIVE"
#
#   private_cluster_config {
#     enable_private_nodes   = true
#     master_ipv4_cidr_block = "172.16.0.32/28"
#   }
#
#   initial_node_count = 1
#
#   node_config {
#     machine_type = "e2-medium"
#   }
#
#   ip_allocation_policy {
#     cluster_secondary_range_name  = "my-pod-range"
#     services_secondary_range_name = "my-svc-range"
#   }
#
#   master_authorized_networks_config {
#     cidr_blocks {
#       cidr_block   = "${google_compute_instance.src_vm.network_interface.0.access_config.0.nat_ip}/32"
#       display_name = google_compute_instance.src_vm.name
#     }
#   }
#
#   deletion_protection = false
# }
