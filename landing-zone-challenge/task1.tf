# -----------------------------------------------
# Task 1. Configure the Shared VPC and service project
# -----------------------------------------

# Equivalent code from in the bucket provided by the lab
locals {
  subnet_01 = "${var.network_name}-subnet-01"
  subnet_02 = "${var.network_name}-subnet-02"
}

module "shared-vpc" {
  source  = "terraform-google-modules/network/google"
  version = "18.3.1"

  project_id                             = var.project_id
  network_name                           = var.network_name
  delete_default_internet_gateway_routes = true

  subnets = [
    {
      subnet_name   = local.subnet_01
      subnet_ip     = "172.16.10.0/24" #"10.10.10.0/24"
      subnet_region = "${var.compute_region}"
      # subnet_private_access = true # Uncomment for Task 2.
    },
    {
      subnet_name      = local.subnet_02
      subnet_ip        = "172.16.20.0/24" #"10.10.20.0/24"
      subnet_region    = "${var.compute_region}"
      subnet_flow_logs = true
      # subnet_private_access = true # Uncomment for Task 2.
    },
  ]

  secondary_ranges = {
    (local.subnet_01) = [
      {
        range_name    = "${local.subnet_01}-01"
        ip_cidr_range = "192.168.64.0/24"
      },
      {
        range_name    = "${local.subnet_01}-02"
        ip_cidr_range = "192.168.65.0/24"
      },
    ]

    (local.subnet_02) = [
      {
        range_name    = "${local.subnet_02}-01"
        ip_cidr_range = "192.168.66.0/24"
      },
    ]
  }
}

data "google_service_account" "service-project-sa" {
  account_id = var.secondary_project_id
  project    = var.secondary_project_id
}

resource "google_compute_shared_vpc_host_project" "host" {
  project = module.shared-vpc.project_id
}

resource "google_compute_shared_vpc_service_project" "service" {
  host_project    = google_compute_shared_vpc_host_project.host.project
  service_project = var.secondary_project_id
}

# The console "attach projects" flow grants the Network User role at the HOST
# PROJECT level (in addition to subnet-level grants). 
# The fabric module only creates subnet-level bindings, so replicate the project-level grant here.
resource "google_project_iam_member" "service-project-network-user" {
  project = var.project_id
  role    = "roles/compute.networkUser"
  member  = data.google_service_account.service-project-sa.member

  depends_on = [google_compute_shared_vpc_host_project.host]
}

# Had issues trying to pass Progress Check using the fabric module.
# Ultimately ended up using the bare resources.
#
# module "shared-vpc-access" {
#   source  = "terraform-google-modules/network/google//modules/fabric-net-svpc-access"
#   version = "18.3.1"
#
#   # The service project attachment API requires the host flag to exist first.
#   depends_on = [google_compute_shared_vpc_host_project.host]
#
#   host_project_id     = var.project_id
#   service_project_ids = [var.secondary_project_id]
#   host_subnets        = module.shared-vpc.subnets_names
#   host_subnet_regions = module.shared-vpc.subnets_regions
#   host_subnet_users = {
#     # The module expects a comma-separated member string per subnet.
#     for v in module.shared-vpc.subnets_names :
#     v => data.google_service_account.service-project-sa.member
#   }
# }
