variable "url_map" {
  type = string
  description = "The self_link or id of the target GCP URL map"
}

variable "name" {
  type        = string
  description = "Base name for the proxy and forwarding rules."
}

variable "ipv4_address" {
  type        = string
  default     = null # Default to ephemeral
  description = "Optional static IPv4 address."
}

variable "ipv6_address" {
  type        = string
  default     = null # Default to ephemeral
  description = "Optional static IPv6 address."
}
