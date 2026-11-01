terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "8.6.0"
    }
    null = {
      source = "hashicorp/null"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.6"
    }
  }

  backend "local" {
    path = "./terraform.tfstate"
  }
}

provider "google" {
  # Configuration options
  project = var.project_id
  region  = var.compute_region
  zone    = var.compute_zone
}

# -----------------------------------------------
# Task 1. Create development VPC manually
# -----------------------------------------------

resource "google_compute_network" "dev-vpc" {
  name                    = "griffin-dev-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "dev-wp" {
  name          = "griffin-dev-wp"
  ip_cidr_range = "192.168.16.0/20"
  network       = google_compute_network.dev-vpc.self_link
  region        = var.compute_region
}

resource "google_compute_subnetwork" "dev-mgmt" {
  name          = "griffin-dev-mgmt"
  ip_cidr_range = "192.168.32.0/20"
  network       = google_compute_network.dev-vpc.self_link
  region        = var.compute_region
}

# -----------------------------------------------
# Task 2. Create production VPC manually
# -----------------------------------------------

resource "google_compute_network" "prod-vpc" {
  name                    = "griffin-prod-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "prod-wp" {
  name          = "griffin-prod-wp"
  ip_cidr_range = "192.168.48.0/20"
  network       = google_compute_network.prod-vpc.self_link
  region        = var.compute_region
}

resource "google_compute_subnetwork" "prod-mgmt" {
  name          = "griffin-prod-mgmt"
  ip_cidr_range = "192.168.64.0/20"
  network       = google_compute_network.prod-vpc.self_link
  region        = var.compute_region
}

# -----------------------------------------------
# Task 3. Create bastion host
# -----------------------------------------------

resource "google_compute_instance" "bastion" {
  name         = "griffin-bastion"
  machine_type = "e2-medium"
  zone         = var.compute_zone
  tags         = ["bastion"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
    }
  }

  network_interface {
    network    = google_compute_network.dev-vpc.id
    subnetwork = google_compute_subnetwork.dev-mgmt.id
    access_config {
    }
  }

  network_interface {
    network    = google_compute_network.prod-vpc.id
    subnetwork = google_compute_subnetwork.prod-mgmt.id
    access_config {
    }
  }
}

resource "google_compute_firewall" "dev-allow-ssh" {
  name          = "griffin-dev-allow-ingress-ssh"
  direction     = "INGRESS"
  network       = google_compute_network.dev-vpc.id
  target_tags   = ["bastion"]
  source_ranges = ["0.0.0.0/0"]

  allow {
    protocol = "tcp"
    ports    = [22]
  }
}

resource "google_compute_firewall" "prod-allow-ssh" {
  name          = "griffin-prod-allow-ingress-ssh"
  direction     = "INGRESS"
  network       = google_compute_network.prod-vpc.id
  target_tags   = ["bastion"]
  source_ranges = ["0.0.0.0/0"]

  allow {
    protocol = "tcp"
    ports    = [22]
  }
}

# -----------------------------------------------
# Task 4. Create and configure Cloud SQL Instance
# -----------------------------------------------

resource "google_sql_database" "wp-dev" {
  name     = "wordpress"
  instance = google_sql_database_instance.wp-dev-db.id
}

resource "google_sql_database_instance" "wp-dev-db" {
  name             = "griffin-dev-db"
  region           = var.compute_region
  database_version = "MYSQL_8_0"

  settings {
    tier = "db-f1-micro"

    database_flags {
      name  = "cloudsql_iam_authentication"
      value = "on"
    }
  }

  deletion_protection = false
}

resource "google_sql_user" "wp-user" {
  name     = "wp_user"
  password = var.db_password
  instance = google_sql_database_instance.wp-dev-db.id
}

resource "google_sql_user" "wp-iam-user" {
  name     = var.email1
  type     = "CLOUD_IAM_USER"
  instance = google_sql_database_instance.wp-dev-db.id
}

# -----------------------------------------------
# Task 5. Create Kubernetes cluster
# -----------------------------------------------

resource "google_container_cluster" "dev-cluster" {
  name               = "griffin-dev"
  network            = google_compute_network.dev-vpc.self_link
  subnetwork         = google_compute_subnetwork.dev-wp.self_link
  location           = var.compute_zone
  initial_node_count = 2

  node_config {
    machine_type = "e2-standard-4"
  }

  deletion_protection = false
}

# -----------------------------------------------
# Task 6. Prepare the Kubernetes cluster
# -----------------------------------------------
# The lab's Task 6 gcloud/kubectl commands, run once after the cluster
# exists. `set -e` makes any failure fail the whole apply — the old
# version ended with `rm key.json`, whose clean exit code masked any
# gcloud/kubectl failure: the apply "succeeded" while the secret was
# never created. The trap removes key.json in every outcome.
resource "null_resource" "cluster-creds" {
  provisioner "local-exec" {
    command = <<-EOT
    set -e
    trap 'rm -f key.json' EXIT

    # The service account may not exist yet in a fresh lab project;
    # create it if needed so the key below can be minted.
    gcloud iam service-accounts create cloud-sql-proxy 2>/dev/null || \
      echo "cloud-sql-proxy service account already exists"

    gcloud container clusters get-credentials ${google_container_cluster.dev-cluster.name} \
      --zone ${var.compute_zone}

    gcloud iam service-accounts keys create key.json \
      --iam-account=cloud-sql-proxy@${var.project_id}.iam.gserviceaccount.com

    kubectl create secret generic cloudsql-instance-credentials \
      --from-file key.json
    EOT
  }
}

# The cloudsql-proxy sidecar authorizes against the SQL instance as this
# service account, which requires the Cloud SQL client role. GCP accepts
# bindings to members that don't exist yet (they activate once the
# service account is created), so order doesn't matter.
resource "google_project_iam_member" "cloudsql-client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:cloud-sql-proxy@${var.project_id}.iam.gserviceaccount.com"
}

resource "local_file" "wp-env-yaml" {
  filename = "${path.module}/wp-env.yaml"
  content = templatefile("${path.module}/templates/wp-env.yaml.tftpl", {
    DATABASE_USERNAME = google_sql_user.wp-user.name
    DATABASE_PASSWORD = var.db_password
  })
  file_permission = "0600"

  # kubectl needs the kubeconfig written by cluster-creds first.
  depends_on = [null_resource.cluster-creds]

  provisioner "local-exec" {
    command = <<-EOT
    kubectl apply -f ${self.filename}
    EOT
  }
}

resource "local_file" "wp-deployment-yaml" {
  filename = "${path.module}/wp-deployment.yaml"
  content = templatefile("${path.module}/templates/wp-deployment.yaml.tftpl", {
    CLOUD_SQL_CONNECTION_NAME = google_sql_database_instance.wp-dev-db.connection_name
  })
  file_permission = "0600"

  # The deployment's pods need both secrets; never apply it before
  # cluster-creds (kubeconfig) or wp-env-yaml (database secret).
  depends_on = [
    null_resource.cluster-creds,
    local_file.wp-env-yaml,
  ]

  provisioner "local-exec" {
    command = <<-EOT
    kubectl apply -f ${self.filename}
    EOT
  }
}

resource "null_resource" "wp-service-yaml" {
  # Apply after the deployment. The absolute path keeps it working
  # regardless of the directory terraform is invoked from.
  depends_on = [
    null_resource.cluster-creds,
    local_file.wp-deployment-yaml,
  ]

  provisioner "local-exec" {
    command = <<-EOT
    kubectl apply -f ${path.module}/wp-service.yaml
    
    kubectl wait --for=jsonpath='{.status.loadBalancer.ingress[0].ip}' svc/wordpress --timeout=180s

    ip=$(kubectl get svc wordpress -o jsonpath='{.status.loadBalancer.ingress[0].ip')
    [ -n "$ip" ] || { echo "load balancer IP came back empty" >&2; exit 1; }
    echo "uptime_host = \"$ip\"" > ${path.module}/uptime.auto.tfvars
    EOT
  }
}

# -----------------------------------------------
# Task 8. Enable monitoring
# -----------------------------------------------

resource "google_monitoring_uptime_check_config" "http" {
  # var.uptime_host is a string (the LB IP), so it can't be used directly
  # as a boolean. Skipped on the first apply, when the IP isn't known
  # yet; set the variable afterwards and apply a second time.
  count = var.uptime_host != null ? 1 : 0

  display_name       = "griffin-wp-uptime-check"
  timeout            = "60s"
  log_check_failures = true

  http_check {
    path           = "/"
    port           = "80"
    request_method = "GET"
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = var.uptime_host
    }
  }

  # The host is the WordPress load balancer IP, which only exists once
  # the service has been applied.
  depends_on = [null_resource.wp-service-yaml]
}

# -----------------------------------------------
# Task 9. Provide access for an additional engineer
# -----------------------------------------------

resource "google_project_iam_member" "editor-member" {
  project = var.project_id
  role    = "roles/editor"
  member  = "user:${var.email2}"
}

