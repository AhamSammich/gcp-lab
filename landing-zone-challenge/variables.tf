variable "project_id" {
    description = "The project ID provided by the lab"
    type = string
}

variable "compute_region" {
    description = "The region designated by the lab"
    type = string
}

variable "compute_zone" {
    description = "The zone designated by the lab"
    type = string
}

variable "gcs_bucket_name" {
    description = "Bucket Name"
    type = string
}

variable "secondary_project_id" {
    description = "Service Project ID"
    type = string
}

variable "network_name" {
    description = "Network Name"
    type = string
}

variable "firewall_name" {
    description = "Firewall Rule Name"
    type = string
}

variable "router_name" {
    description = "Cloud Router Name"
    type = string
}

variable "instance_name" {
    description = "Instance Name"
    type = string
}

variable "lab_region" {
    description = "Lab Region"
    type = string
}

variable "gcs_bucket_name_1" {
    description = "Service Project Bucket Name"
    type = string
}

variable "subnetwork" {
    description = "Subnetwork Link"
    type = string
}

variable "address_name" {
    description = "Static IP Name"
    type = string
}
