terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "8.2.0"
    }
  }

  backend "local" {
    path = "./terraform.tfstate"
  }
}

provider "google" {
  # Configuration options
}

# -----------------------------------------------
# Task 1. Create a GKE cluster
# -----------------------------------------------

resource "google_container_cluster" "cluster_1" {
  name     = var.cluster_name
  location = var.cluster_zone

  remove_default_node_pool = true
  initial_node_count       = 1

  monitoring_config {
    managed_prometheus {
      enabled = true

      auto_monitoring_config {
        scope = "ALL"
      }
    }
  }
}

resource "google_container_node_pool" "pool_1" {
  name = "pool-1"
  cluster  = google_container_cluster.cluster_1.name
  location = var.cluster_zone
  initial_node_count       = 3

  lifecycle {
    ignore_changes = [
      initial_node_count
    ]
  }

  autoscaling {
    min_node_count = 2
    max_node_count = 6
  }
}

# -----------------------------------------------
# Task 4. Create a logs-based metric and alerting policy
# -----------------------------------------------

resource "google_logging_metric" "logging_metric" {
  name   = "pod-image-errors"
  filter = "resource.type=k8s_pod AND severity>=WARNING"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_monitoring_alert_policy" "alert_policy" {
  display_name = "Pod Error Alert"
  combiner = "OR"
  enabled = true
  
  conditions {
    display_name = "Metric - ${google_logging_metric.logging_metric.id}"
    condition_threshold {
      duration = "0s"
      filter = "resource.type = \"k8s_pod\" AND metric.type = \"logging.googleapis.com/user/${google_logging_metric.logging_metric.id}\""
      comparison = "COMPARISON_GT"
      threshold_value = 0

      aggregations {
        per_series_aligner = "ALIGN_COUNT"
        cross_series_reducer = "REDUCE_NONE"
        alignment_period = "600s"
      }

      trigger {
        count = 1
      }
    }
  }

  alert_strategy {
    notification_prompts = ["OPENED"]
  }
}

# -----------------------------------------------
# Task 6. Containerize your code and deploy it onto the cluster
# -----------------------------------------------

resource "google_artifact_registry_repository" "repo" {
  repository_id = var.repo_name
  location = "us"
  format = "DOCKER"
}

