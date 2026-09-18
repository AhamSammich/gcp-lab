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
# Task 1. Create a custom security role
# -----------------------------------------------

module "iam_custom_security_role" {
  source  = "terraform-google-modules/iam/google//modules/custom_role_iam"
  version = "8.2.0"

  role_id   = var.role_name
  target_id = var.project_id

  permissions = [
    "storage.buckets.get",
    "storage.objects.get",
    "storage.objects.list",
    "storage.objects.update",
    "storage.objects.create",
  ]
}

# -----------------------------------------------
# Task 2. Create a service account
# -----------------------------------------------

resource "google_service_account" "sa" {
  account_id = var.service_account_id
  project    = var.project_id
}

# -----------------------------------------------
# Task 3. Bind a custom security role to a service account
# -----------------------------------------------

module "projects_iam_bindings" {
  source  = "terraform-google-modules/iam/google//modules/projects_iam"
  version = "8.2.0"

  projects = [var.project_id]
  mode     = "additive"

  bindings = {
    "${module.iam_custom_security_role.custom_role_name}" = [
      google_service_account.sa.member
    ],
    "roles/monitoring.viewer" = [
      google_service_account.sa.member
    ],
    "roles/monitoring.metricWriter" = [
      google_service_account.sa.member
    ],
    "roles/logging.logWriter" = [
      google_service_account.sa.member
    ],
  }
}

# -----------------------------------------------
# Task 4. Create and configure a new Kubernetes Engine private cluster
# -----------------------------------------------

data "google_compute_instance" "jumphost" {
  name = "orca-jumphost"
  zone = var.zone
}

resource "google_container_cluster" "cluster1" {
  name            = var.cluster_name
  location        = var.zone
  network         = "orca-build-vpc"
  subnetwork      = "orca-build-subnet"
  networking_mode = "VPC_NATIVE"

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = true
  }

  master_authorized_networks_config {
    cidr_blocks {
      cidr_block   = "${data.google_compute_instance.jumphost.network_interface.0.network_ip}/32"
      display_name = "orca-jumphost"
    }
  }

  node_config {
    service_account = google_service_account.sa.email
    oauth_scopes    = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]
  }

  initial_node_count  = 1
  deletion_protection = false
}

