# Target HTTP Proxies (One per IP version)
resource "google_compute_target_http_proxy" "ipv4" {
  name    = "${var.name}-http-proxy-v4"
  url_map = var.url_map
}

resource "google_compute_target_http_proxy" "ipv6" {
  name    = "${var.name}-http-proxy-v6"
  url_map = var.url_map
}

# Forwarding Rules
resource "google_compute_global_forwarding_rule" "ipv4" {
  name       = "${var.name}-fw-v4"
  target     = google_compute_target_http_proxy.ipv4.id
  ip_version = "IPV4"
  ip_address = var.ipv4_address
  port_range = "80"
  ip_protocol = "TCP"
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

resource "google_compute_global_forwarding_rule" "ipv6" {
  name       = "${var.name}-fw-v6"
  target     = google_compute_target_http_proxy.ipv6.id
  ip_version = "IPV6"
  ip_address = var.ipv6_address
  port_range = "80"
  ip_protocol = "TCP"
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

