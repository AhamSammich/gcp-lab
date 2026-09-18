variable "project_id" {
  description = "The project ID provided by the lab"
  type        = string
}

variable "region" {
  description = "The region designated by the lab"
  type        = string
}

variable "zone" {
  description = "The zone designated by the lab"
  type        = string
}

variable "cluster_name" {
  description = "The name of the deployed cluster"
  type        = string
}

variable "role_name" {
  description = "The name of the custom security role"
  type        = string
}

variable "service_account_id" {
  description = "The ID for the create service account"
  type        = string
}

