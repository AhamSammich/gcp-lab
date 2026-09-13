output "ipv4_address" {
  description = "IPv4 address of the created forwarding rule"
  value = google_compute_global_forwarding_rule.ipv4.ip_address
}

output "ipv6_address" {
  description = "IPv6 address of the created forwarding rule"
  value = google_compute_global_forwarding_rule.ipv6.ip_address
}

