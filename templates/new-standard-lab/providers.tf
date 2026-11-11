terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "8.6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.compute_region
  zone    = var.compute_zone
}