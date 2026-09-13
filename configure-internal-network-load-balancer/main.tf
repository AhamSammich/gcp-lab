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
# Task 1. Configure internal traffic and 
#         health check firewall rules
# -----------------------------------------------

resource "google_compute_firewall" "fw-allow-lb-access" {
  name        = "fw-allow-lb-access"
  network     = "my-internal-app"
  target_tags = ["backend-service"]

  allow {
    protocol = "tcp"
  }

  allow {
    protocol = "udp"
  }

  allow {
    protocol = "icmp"
  }

  source_ranges = ["10.10.0.0/16"]
}


resource "google_compute_firewall" "fw-allow-health-checks" {
  name        = "fw-allow-health-checks"
  network     = "my-internal-app"
  target_tags = ["backend-service"]

  allow {
    protocol = "tcp"
    ports    = [80]
  }

  source_ranges = ["130.211.0.0/22", "35.191.0.0/16"]
}

# -----------------------------------------------
# Task 2. Create a NAT configuration using Cloud Router
# -----------------------------------------------

module "nat-config" {
  source  = "terraform-google-modules/cloud-nat/google"
  version = "7.0.0"

  project_id = var.project_id
  region     = var.compute_region
  router     = "nat-router-${var.compute_region}"

  name          = "nat-config"
  network       = "my-internal-app"
  create_router = true
}

# -----------------------------------------------
# Task 3. Configure instance templates and 
#         create instance groups
# -----------------------------------------------

resource "google_compute_instance" "utility-vm" {
  name         = "utility-vm"
  machine_type = "e2-medium"
  zone         = var.compute_zone_3

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
    }
  }

  network_interface {
    network    = "my-internal-app"
    subnetwork = "subnet-a"
    network_ip = "10.10.20.50"
  }
}

# -----------------------------------------------
# Task 4. Configure the internal Network Load Balancer
# -----------------------------------------------

resource "google_compute_region_backend_service" "my-ilb" {
  name                  = "my-ilb"
  region                = var.compute_region
  health_checks         = [google_compute_health_check.my-ilb-health-check.id]
  load_balancing_scheme = "INTERNAL"
  protocol              = "TCP"
  network               = "my-internal-app"

  backend {
    group          = "projects/${var.project_id}/zones/${var.compute_zone_2}/instanceGroups/instance-group-1"
    balancing_mode = "CONNECTION"
  }

  backend {
    group          = "projects/${var.project_id}/zones/${var.compute_zone}/instanceGroups/instance-group-2"
    balancing_mode = "CONNECTION"
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

resource "google_compute_health_check" "my-ilb-health-check" {
  name                = "my-ilb-health-check"
  check_interval_sec  = 10
  timeout_sec         = 5
  healthy_threshold   = 2
  unhealthy_threshold = 5

  tcp_health_check {
    port = 80
  }

  log_config {
    enable = true
  }
}

resource "google_compute_forwarding_rule" "my-ilb-ip" {
  name   = "my-ilb-ip"
  region = var.compute_region

  load_balancing_scheme = "INTERNAL"
  backend_service       = google_compute_region_backend_service.my-ilb.self_link
  ports                 = [80]
  network               = "my-internal-app"
  subnetwork            = "subnet-b"
  ip_version            = "IPV4"
  ip_address            = "10.10.30.5"
  ip_protocol           = "TCP"
}

