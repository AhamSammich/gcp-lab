variable "base_instance_name" {
  description = "The base name for individual instances"
  type = string
}

variable "instance_group_name" {
  description = "The name for the instance group"
  type = string
}

variable "instance_group_region" {
  description = "The region for the instance group"
  type = string
}

variable "instance_template" {
  description = "The instance template for the instance group"
  type = string
}

variable "health_check" {
  description = "The health_check for the instance group"
  type = string
}

