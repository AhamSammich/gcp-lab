variable "network" {
  description = "The `name` or `self_link` of the network"
  type        = string
}

variable "instance_name" {
  description = "The name of the VM instance"
  type        = string
}

variable "instance_zone" {
  description = "The location of the VM instance"
  type        = string
  default = "default"
}

variable "tags" {
  description = "List of tags for the VM instance"
  type = list(string)
  default = []
}
