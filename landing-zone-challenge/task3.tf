# -----------------------------------------------
# Task 3. Deploy a Secure VM Instance and Reserve an Internal Static IP
# -----------------------------------------------

# Lab directs student to clone the entire repo.
# We can just pull from the remote source instead.
module "service-vm-template" {
  source  = "terraform-google-modules/vm/google//modules/instance_template"
  version = "15.4.1"

  project_id         = var.secondary_project_id
  region             = var.lab_region
  name_prefix        = var.instance_name
  subnetwork_project = var.project_id
  subnetwork         = var.subnetwork # Cross-project resource
  metadata           = { serial-port-enable = "FALSE" }

  # The template references a host-project subnet cross-project, which is only
  # valid once the service project is attached via Shared VPC.
  depends_on = [google_compute_shared_vpc_service_project.service]
}

module "service-vm" {
  source  = "terraform-google-modules/vm/google//modules/compute_instance"
  version = "15.4.1"

  instance_template = module.service-vm-template.self_link_unique
  project_id        = var.secondary_project_id
  hostname          = var.instance_name
  static_ips        = [google_compute_address.service-static-ip.address]
}

# Equivalent code from in the bucket provided by the lab
resource "google_compute_address" "service-static-ip" {
  # Internal addresses must be reserved in the project where the instance
  # lives (the service project), referencing the shared subnet in the host
  # project — cross-project *use* of an internal address is not allowed.
  project      = google_compute_shared_vpc_service_project.service.service_project
  region       = var.compute_region
  subnetwork   = var.subnetwork # Cross-project resource
  name         = var.address_name
  address_type = "INTERNAL"
}
