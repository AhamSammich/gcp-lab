# -----------------------------------------------
# Task 2. Secure the network and enforce policies
# -----------------------------------------------

# Enable Private Google Access by adding `subnet_private_access = true`
# to the subnets in the `shared-vpc` module from Task 1.

# Create firewall rule to restrict SSH to IAP ranges
resource "google_compute_firewall" "allow-ingress-iap" {
  network       = module.shared-vpc.network_self_link
  name          = var.firewall_name
  direction     = "INGRESS"
  priority      = 1000
  source_ranges = ["35.235.240.0/20"]

  allow {
    protocol = "tcp"
    ports    = [22]
  }
}

# Create a Cloud NAT in the Host Project
module "host-nat" {
  source     = "terraform-google-modules/cloud-nat/google"
  version    = "7.1.0"
  depends_on = [module.shared-vpc]

  project_id                         = var.project_id
  name                               = "cymbal-gateway"
  network                            = var.network_name
  region                             = var.lab_region
  router                             = var.router_name
  create_router                      = true
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}
