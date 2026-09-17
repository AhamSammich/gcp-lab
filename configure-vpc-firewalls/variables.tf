variable "project_id" {
  description = "The project ID provided by the lab"
  type        = string
}

variable "compute_zone_1" {
  description = "The first zone designated by the lab"
  type        = string
}

variable "compute_zone_2" {
  description = "The second zone designated by the lab"
  type        = string
}

variable "shell_ip_address" {
  description = "IP address of the Cloud Shell instance"
  type        = string
}
