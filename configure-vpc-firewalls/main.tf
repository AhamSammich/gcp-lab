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
# Task 1. Create VPC network and instances
# -----------------------------------------------

module "mynetwork" {
  source  = "terraform-google-modules/network/google"
  version = "18.3.0"

  network_name            = "mynetwork"
  project_id              = var.project_id
  subnets                 = []
  auto_create_subnetworks = true
}

module "default_vm" {
  source = "./modules/instances"

  instance_name = "default-vm-1"
  instance_zone = var.compute_zone_1
  network       = "default"
}

module "mynet_vm1" {
  source = "./modules/instances"

  instance_name = "mynet-vm-1"
  instance_zone = var.compute_zone_1
  network       = module.mynetwork.network_name
  tags          = ["lab-ssh"]
}

module "mynet_vm2" {
  source = "./modules/instances"

  instance_name = "mynet-vm-2"
  instance_zone = var.compute_zone_2
  network       = module.mynetwork.network_self_link
  tags          = ["lab-ssh"]
}

# -----------------------------------------------
# Task 4. Create custom ingress firewall rules
# -----------------------------------------------

module "firewall_rules" {
  source  = "terraform-google-modules/network/google//modules/firewall-rules"
  version = "18.3.0"

  project_id   = var.project_id
  network_name = module.mynetwork.network_name

  ingress_rules = [
    {
      name          = "mynetwork-ingress-allow-ssh-from-cs"
      source_ranges = [var.shell_ip_address]
      allow = [
        {
          protocol      = "tcp"
          ports         = [22]
        },
      ]
      target_tags   = ["lab-ssh"]
    },
    {
      name          = "mynetwork-ingress-allow-icmp-internal"
      source_ranges = ["10.128.0.0/9"]
      allow = [
        {
          protocol      = "icmp"
        },
      ]
    },
    # -----------------------------------------------
    # Task 5. Set the firewall rule priority
    # -----------------------------------------------

    # {
    #   name = "mynetwork-ingress-deny-icmp-all"
    #   deny = [
    #     {
    #       protocol      = "icmp"
    #     },
    #   ]
    #   priority = 500
    #   # priority = 2000
    # },
  ]

  # -----------------------------------------------
  # Task 6. Configure egress firewall rules
  # -----------------------------------------------

  # egress_rules = [
  #   {
  #     name = "mynetwork-egress-deny-icmp-all"
  #     deny = [
  #       {
  #         protocol      = "icmp"
  #       },
  #     ]
  #     priority = 10000
  #   }
  # ]
}
