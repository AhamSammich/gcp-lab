variable "project_id" {
  description = "The project ID provided by the lab"
  type        = string
}

variable "compute_region" {
  description = "The region designated by the lab"
  type        = string
}

variable "compute_zone" {
  description = "The zone designated by the lab"
  type        = string
}

variable "email1" {
  description = "Lab student email"
  type        = string
}

variable "email2" {
  description = "Lab student email"
  type        = string
}

variable "db_password" {
  description = "Password for the wp_user Cloud SQL user (also templated into the database secret)"
  type        = string
  sensitive   = true
}

variable "uptime_host" {
  description = "IP address for the uptime check"
  type        = string
  default     = null
}
