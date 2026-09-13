resource "google_compute_region_instance_group_manager" "mig" {
  base_instance_name = var.base_instance_name
  name               = var.instance_group_name
  region             = var.instance_group_region

  version {
    instance_template = var.instance_template
  }

  named_port {
    name = "http"
    port = 80
  }

  auto_healing_policies {
    health_check      = var.health_check
    initial_delay_sec = 60
  }
}

resource "google_compute_region_autoscaler" "mig-autoscaler" {
  name   = "${var.instance_group_name}-autoscaler"
  target = google_compute_region_instance_group_manager.mig.self_link
  region = var.instance_group_region

  autoscaling_policy {
    min_replicas    = 1
    max_replicas    = 2
    cooldown_period = 60
  }
}

