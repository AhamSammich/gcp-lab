terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "8.3.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "3.2.1"
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
# GKE CLUSTER & AUTHENTICATION
# Note: The GKE cluster is pre-provisioned by the lab environment.
# -----------------------------------------------

data "google_client_config" "default" {}

data "google_container_cluster" "existing" {
  name     = "autopilot-cluster-1"
  location = var.region
}

provider "kubernetes" {
  host  = "https://${data.google_container_cluster.existing.endpoint}"
  token = data.google_client_config.default.access_token
  cluster_ca_certificate = base64decode(
    data.google_container_cluster.existing.master_auth[0].cluster_ca_certificate
  )
}

# -----------------------------------------------
# LOCAL VARIABLES
# -----------------------------------------------

locals {
  apis = [
    "sql-component.googleapis.com",
    "sqladmin.googleapis.com",
  ]
  kube_ns      = "default"
  kube_sa_name = "gkesqlsa"
  sql_credentials = {
    username = "sqluser"
    password = "sqlpassword"
  }
  sql_instance_name = "sql-instance"
}

# -----------------------------------------------
# GOOGLE CLOUD
# -----------------------------------------------

# Enable required APIs
resource "google_project_service" "enabled_apis" {
  for_each = toset(local.apis)

  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

# Create a GCP service account with Cloud SQL access
resource "google_service_account" "sql_sa" {
  account_id   = "sql-access"
  display_name = "sql-access"
}

resource "google_project_iam_member" "sql_sa_iam" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = google_service_account.sql_sa.member
}

# Create the Cloud SQL instance and user
resource "google_sql_database_instance" "db_instance" {
  name             = local.sql_instance_name
  region           = var.region
  database_version = "MYSQL_8_0"

  settings {
    tier = "db-n1-standard-2"
  }

  deletion_protection = false
}

resource "google_sql_user" "db_user" {
  name                = local.sql_credentials.username
  instance            = google_sql_database_instance.db_instance.name
  password_wo         = local.sql_credentials.password
  password_wo_version = 1
}

# Create Workload Identity to link GCP and Kubernetes service accounts
resource "google_service_account_iam_member" "gke_sql_sa" {
  service_account_id = google_service_account.sql_sa.id
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${local.kube_ns}/${local.kube_sa_name}]"
  depends_on         = [kubernetes_service_account_v1.kube_sa]
}

# -----------------------------------------------
# KUBERNETES
# -----------------------------------------------

# Create Kubernetes service account with identity annotation
resource "kubernetes_service_account_v1" "kube_sa" {
  metadata {
    name      = local.kube_sa_name
    namespace = local.kube_ns
    annotations = {
      "iam.gke.io/gcp-service-account" = google_service_account.sql_sa.email
    }
  }
}

# Create secret for database credentials
resource "kubernetes_secret_v1" "sql_creds" {
  metadata {
    name      = "sql-credentials"
    namespace = kubernetes_service_account_v1.kube_sa.metadata[0].namespace
  }

  data_wo = local.sql_credentials

  data_wo_revision = 1
  type             = "generic"
}

# Deploy and expose application
resource "kubernetes_deployment_v1" "wp_deployment" {
  metadata {
    name      = "wordpress"
    namespace = kubernetes_service_account_v1.kube_sa.metadata[0].namespace
    labels = {
      app = "wordpress"
    }
  }

  spec {
    selector {
      match_labels = {
        app = "wordpress"
      }
    }

    template {
      metadata {
        labels = {
          app = "wordpress"
        }
      }

      spec {
        service_account_name = kubernetes_service_account_v1.kube_sa.metadata[0].name

        container {
          name  = "web"
          image = "gcr.io/cloud-marketplace/google/wordpress:6.1"

          port {
            container_port = 80
          }

          env {
            name  = "WORDPRESS_DB_HOST"
            value = "127.0.0.1:3306"
          }
          env {
            name = "WORDPRESS_DB_USER"
            value_from {
              secret_key_ref {
                name = "sql-credentials"
                key  = "username"
              }
            }
          }
          env {
            name = "WORDPRESS_DB_PASSWORD"
            value_from {
              secret_key_ref {
                name = "sql-credentials"
                key  = "password"
              }
            }
          }
        }

        container {
          name  = "cloudsql-proxy"
          image = "gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.8.0"
          args = [
            "--structured-logs",
            "--port=3306",
            "${var.project_id}:${var.region}:${local.sql_instance_name}",
          ]
          security_context {
            run_as_non_root = true
          }
        }
      }
    }
  }
}

resource "kubernetes_service_v1" "wp_service" {
  metadata {
    name      = "wordpress-service"
    namespace = kubernetes_deployment_v1.wp_deployment.metadata[0].namespace
    labels = {
      app = "wordpress"
    }
  }

  spec {
    type = "LoadBalancer"
    selector = {
      app = "wordpress"
    }

    port {
      protocol = "TCP"
      port     = 80
    }
  }
}
