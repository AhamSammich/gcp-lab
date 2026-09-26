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

data "google_compute_network" "default" {
  name    = "default"
  project = var.project_id
}


# -----------------------------------------------
# Task 1. Enable APIs
# -----------------------------------------------

locals {
  services = [
    "compute",
    "dns",
  ]
}

resource "google_project_service" "required_api" {
  for_each = toset(local.services)

  project            = var.project_id
  service            = "${each.key}.googleapis.com"
  disable_on_destroy = false
}


# -----------------------------------------------
# Task 2. Configure the firewall
# -----------------------------------------------

module "firewall_rules" {
  source  = "terraform-google-modules/network/google//modules/firewall-rules"
  version = "18.3.0"

  project_id   = var.project_id
  network_name = "default"

  ingress_rules = [
    {
      name          = "fw-default-iapproxy"
      priority      = 1000
      source_ranges = ["35.235.240.0/20"]
      allow = [
        { protocol = "tcp", ports = [22] },
        { protocol = "icmp" },
      ]
    },
    {
      name          = "allow-http-traffic"
      priority      = 1000
      source_ranges = ["0.0.0.0/0"]
      target_tags   = ["http-server"]
      allow = [
        { protocol = "tcp", ports = [80] },
      ]
    },
  ]
}


# -----------------------------------------------
# Task 3. Launch client VMs
# -----------------------------------------------

locals {
  client_instances = {
    us = {
      name   = "us-client-vm"
      region = var.region1
      zone   = var.zone1
    }
    europe = {
      name   = "europe-client-vm"
      region = var.region2
      zone   = var.zone2
    },
    asia = {
      name   = "asia-client-vm"
      region = var.region3
      zone   = var.zone3
    },
  }
}

resource "google_compute_instance" "client_vm" {
  for_each = local.client_instances

  name         = local.client_instances[each.key].name
  zone         = local.client_instances[each.key].zone
  machine_type = "e2-micro"

  boot_disk {
    initialize_params {
      image = "debian-12"
    }
  }

  network_interface {
    network = "default"
    access_config {}
  }
}


# -----------------------------------------------
# Task 4. Launch server VMs
# -----------------------------------------------

locals {
  server_instances = {
    us = {
      name = "us-web-vm"
      location = var.region1 # lab specifically uses region here
      zone = var.zone1
    },
    europe = {
      name = "europe-web-vm"
      location = var.zone2 # lab specifically uses zone here
      zone = var.zone2
    },
  }
}

resource "google_compute_instance" "server_vm" {
  for_each = local.server_instances

  name         = local.server_instances[each.key].name
  zone         = local.server_instances[each.key].zone
  machine_type = "e2-micro"
  tags         = ["http-server"]

  boot_disk {
    initialize_params {
      image = "debian-12"
    }
  }

  network_interface {
    network    = "default"
    subnetwork = "default"
    access_config {}
  }

  metadata_startup_script = <<-EOT
  #!/bin/bash
  apt-get update
  apt-get install apache2 -y
  echo "Page served from: ${local.server_instances[each.key].location}" | tee /var/www/html/index.html
  systemctl restart apache2
  EOT
}


# -----------------------------------------------
# Task 5. Setting up environment variables
# -----------------------------------------------

# **INTERNAL** IPs from the **SERVER** VMs
locals {
  web_ips = {
    us     = google_compute_instance.server_vm["us"].network_interface.0.network_ip
    europe = google_compute_instance.server_vm["europe"].network_interface.0.network_ip
  }
}

/*

**NOTE**

Regardless of the Terraform approach used for the following steps,
the final Progress Check will only pass if the managed-zone and record-set
are created via `gcloud` commands.

However, the verifications for "Task 8. Testing" still exhibit the
desired behavior.

*/

# -----------------------------------------------
# Task 6. Create the private zone
# -----------------------------------------------


# resource "google_dns_managed_zone" "example" {
#   name        = "example"
#   dns_name    = "example.com."
#   description = "test"
#   visibility  = "private"
#
#   private_visibility_config {
#     networks {
#       network_url = data.google_compute_network.default.id
#     }
#   }
# }


# -----------------------------------------------
# Task 7. Create Cloud DNS Routing Policy
# -----------------------------------------------

# resource "google_dns_record_set" "geo" {
#   name = "geo.${google_dns_managed_zone.example.dns_name}"
#   type = "A"
#   ttl  = 5
#
#   managed_zone = google_dns_managed_zone.example.name
#
#   routing_policy {
#     geo {
#       location = var.region1
#       rrdatas  = [local.web_ips.us]
#     }
#
#     geo {
#       location = var.region2
#       rrdatas  = [local.web_ips.europe]
#     }
#   }
# }
#


# -----------------------------------------------
# Task 6/7. Alternative creation using cloud-dns module
# -----------------------------------------------

module "dns_private_zone" {
  source  = "terraform-google-modules/cloud-dns/google"
  version = "7.2.0"

  project_id  = var.project_id
  name        = "example"
  domain      = "example.com."
  description = "test"
  type        = "private"

  private_visibility_config_networks = [
    data.google_compute_network.default.self_link
  ]

  recordsets = [
    {
      name = "geo"
      type = "A"
      ttl  = 5
      routing_policy = {
        geo = [
          {
            location = var.region1
            records  = [local.web_ips.us]
          },
          {
            location = var.region2
            records  = [local.web_ips.europe]
          },
        ]
      }
    },
  ]
}

