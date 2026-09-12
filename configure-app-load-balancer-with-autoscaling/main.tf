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
  source = "terraform-google-modules/cloud-nat/google"
  version = "7.0.0"

  project_id = var.project_id
  region = var.compute_region
  router = "nat-router-us1"

  name = "nat-config" 
  network = "default"
  create_router = true
}

# -----------------------------------------------
# Task 3. Create a custom image for a web server
# -----------------------------------------------

# Create a webserver instance
# Comment out (delete) after creating image
resource "google_compute_instance" "webserver" {
  name         = "webserver"
  zone         = var.compute_zone
  machine_type = "e2-micro"
  tags         = ["allow-health-checks"]
  desired_state = "TERMINATED"

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
  name            = "mywebserver"
  source_disk = google_compute_instance.webserver.name

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

module "us-1-mig" {
  source  = "terraform-google-modules/vm/google//modules/mig"
  version = "15.2.1"

  instance_template = google_compute_instance_template.mywebserver-template.self_link
  project_id        = var.project_id
  region            = var.compute_region

  mig_name     = "us-1-mig"
  min_replicas = 1
  max_replicas = 2
  autoscaling_cpu = [
    {
      target            = 80
      predictive_method = "NONE"
    }
  ]
  autoscaling_enabled = true
  cooldown_period     = 60

  health_check_name = "http-health-check"
  health_check      = { 
    check_interval_sec : 30,
    enable_logging: false,
    healthy_threshold: 1,
    host: "",
    initial_delay_sec: 60,
    port: 80,
    proxy_header: "NONE",
    request: "",
    request_path: "/"
    response: "",
    timeout_sec: 10
    type: "http",
    unhealthy_threshold: 5
  }
}

module "notus-1-mig" {
  source  = "terraform-google-modules/vm/google//modules/mig"
  version = "15.2.1"

  instance_template = google_compute_instance_template.mywebserver-template.self_link
  project_id        = var.project_id
  region            = var.compute_region_2

  mig_name            = "notus-1-mig"
  min_replicas        = 1
  max_replicas        = 2
  autoscaling_cpu     = [
    { 
      target = 80
      predictive_method = "NONE"
    }
  ]
  autoscaling_enabled = true
  cooldown_period     = 60

  health_check_name = "http-health-check"
  health_check      = { 
    check_interval_sec : 30,
    enable_logging: false,
    healthy_threshold: 1,
    host: "",
    initial_delay_sec: 60,
    port: 80,
    proxy_header: "NONE",
    request: "",
    request_path: "/"
    response: "",
    timeout_sec: 10
    type: "http",
    unhealthy_threshold: 5
  }
}

# -----------------------------------------------
# Task 5. Configure the Application Load Balancer (HTTP)
# -----------------------------------------------

module "lb-http" {
  source  = "terraform-google-modules/lb-http/google"
  version = "14.2.0"

  # insert the 3 required variables here
  backends = {
    default = {
      port = 80
      protocol = "HTTP"

      groups = [
        {
          group = us-1-mig
        },
        {
        }
      ]

    }
  }

  name = "http-backend"
  project = var.project_id
}

