variable "project_id" {
  description = "The project ID provided by the lab"
  type        = string
}

variable "compute_region" {
  description = "The region provided by the lab"
  type        = string
}

variable "compute_zone" {
  description = "The zone provided by the lab"
  type        = string
}

variable "ssh-iap-tag" {
  description = "Target tag for bastion host"
  type        = string
}

variable "ssh-internal-tag" {
  description = "Target tag for internal traffic rule"
  type        = string
}

variable "http-tag" {
  description = "Target tag for HTTP firewall rule"
  type        = string
}

