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
# Task 1. Configure a health check firewall rule
# -----------------------------------------------

resource "google_compute_firewall" "fw-allow-health-checks" {
  name    = "fw-allow-health-checks"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["80"]
  }

  source_ranges = ["130.211.0.0/22", "35.191.0.0/16"]
  target_tags   = ["allow-health-checks"]
}

# -----------------------------------------------
# Task 2. Create a NAT configuration using Cloud Router
# -----------------------------------------------

module "nat-config" {
  source  = "terraform-google-modules/cloud-nat/google"
  version = "7.0.0"

  project_id = var.project_id
  region     = var.compute_region
  router     = "nat-router-us1"

  name          = "nat-config"
  network       = "default"
  create_router = true
}

# -----------------------------------------------
# Task 3. Create a custom image for a web server
# -----------------------------------------------

# Create a webserver instance
# Comment out (delete) after creating image
resource "google_compute_instance" "webserver" {
  name           = "webserver"
  zone           = var.compute_zone
  machine_type   = "e2-micro"
  tags           = ["allow-health-checks"]
  desired_status = "TERMINATED"

  boot_disk {
    auto_delete = false # Persists disk if instance deleted

    initialize_params {
      image = "debian-cloud/debian-12"
    }
  }

  network_interface {
    network = "default"
  }

  metadata_startup_script = <<-EOT
    #!/bin/bash
    sudo apt-get update
    sudo apt-get install -y apache2
    sudo service apache2 start
    sudo systemctl enable apache2
  EOT
}

# -----------------------------------------------
# Task 3.5. Create a custom image for a web server
# -----------------------------------------------
# Create custom image from webserver disk
resource "google_compute_image" "mywebserver" {
  name        = "mywebserver"
  source_disk = google_compute_instance.webserver.boot_disk[0].source

}

# -----------------------------------------------
# Task 4. Configure an instance template and
#         create instance groups
# -----------------------------------------------

# Configure the instance template
resource "google_compute_instance_template" "mywebserver-template" {
  name         = "mywebserver-template"
  machine_type = "e2-micro"
  tags         = ["allow-health-checks"]

  disk {
    source_image = google_compute_image.mywebserver.self_link
  }

  network_interface {
    network = "default"
  }
}

resource "google_compute_health_check" "http-health-check" {
  name = "http-health-check"

  http_health_check {
    port = 80
  }
}

module "mig-us" {
  source = "./modules/mig"

  base_instance_name    = "us-1-mig"
  instance_group_name   = "us-1-mig"
  instance_group_region = var.compute_region
  instance_template     = google_compute_instance_template.mywebserver-template.self_link_unique
  health_check          = google_compute_health_check.http-health-check.self_link
}

module "mig-notus" {
  source = "./modules/mig"

  base_instance_name    = "notus-1-mig"
  instance_group_name   = "notus-1-mig"
  instance_group_region = var.compute_region_2
  instance_template     = google_compute_instance_template.mywebserver-template.self_link_unique
  health_check          = google_compute_health_check.http-health-check.self_link
}

# -----------------------------------------------
# Task 5. Configure the Application Load Balancer (HTTP)
# -----------------------------------------------

resource "google_compute_backend_service" "http-backend" {
  name                  = "http-backend"
  health_checks         = [google_compute_health_check.http-health-check.id]
  protocol              = "HTTP"
  port_name             = "http"
  enable_cdn            = false
  load_balancing_scheme = "EXTERNAL_MANAGED"

  backend {
    balancing_mode        = "RATE"
    group                 = module.mig-us.instance_group
    max_rate_per_instance = 50
    capacity_scaler       = 1
  }

  backend {
    balancing_mode  = "UTILIZATION"
    group           = module.mig-notus.instance_group
    max_utilization = 0.8
    capacity_scaler = 1
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

resource "google_compute_url_map" "http-lb" {
  name            = "http-lb"
  default_service = google_compute_backend_service.http-backend.self_link
}

module "lb-frontend" {
  source = "./modules/frontend"

  name    = "http-lb"
  url_map = google_compute_url_map.http-lb.self_link
}

# -----------------------------------------------
# Task 6. Stress test the Application Load Balancer (HTTP)
# -----------------------------------------------

resource "google_compute_instance" "test-vm" {
  name         = "stress-test"
  zone         = var.compute_zone
  machine_type = "e2-micro"

  boot_disk {
    initialize_params {
      image = google_compute_image.mywebserver.self_link
    }
  }

  network_interface {
    network = "default"
  }

  metadata_startup_script = <<-EOT
    #!/bin/bash
    ab -n 50000 -c 1000 http://${module.lb-frontend.ipv4_address}/
  EOT
}

