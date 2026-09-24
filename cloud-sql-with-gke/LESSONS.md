# DevOps Lessons: GKE + Cloud SQL

A study guide detailing the core architectural patterns taught in the lab, followed by critical gaps and caveats required for a real-world production environment.

---

## Part 1: Core Lab Takeaways & Architecture

The lab demonstrates how to securely connect containerized workloads in GKE to Google Cloud SQL without managing long-lived static credentials or exposing the database directly to the public internet.

### 1. Workload Identity (Zero Static Keys)
* **The Pattern:** Binds a Kubernetes Service Account (KSA) directly to a Google Service Account (GSA) using GCP IAM (`roles/iam.workloadIdentityUser`) and GKE annotations (`iam.gke.io/gcp-service-account`).
* **Why it matters:** Eliminates the need to export, rotate, and mount static JSON service account keys inside Kubernetes pods. The GKE metadata server dynamically provisions short-lived OAuth tokens for pod identity.

### 2. Cloud SQL Auth Proxy Sidecar Pattern
* **The Pattern:** Runs `cloud-sql-proxy` as a co-located container within the same Pod as the application (`web`).
* **Why it matters:**
  * The application communicates with the database locally over loopback (`127.0.0.1:3306`) with zero TLS configuration required in the application code.
  * The proxy intercepts traffic, authenticates using the Pod's Workload Identity, encrypts the connection with mutual TLS (mTLS), and tunnels traffic securely to Cloud SQL.

### 3. Dynamic Terraform Provider Authentication
* **The Pattern:** Injects the GKE cluster endpoint, CA certificate, and Google client OAuth token dynamically into the `kubernetes` provider block.
* **Why it matters:** Removes reliance on host-specific `~/.kube/config` files and local shell commands (`local-exec`), ensuring portability across developer workstations and CI/CD runners.

---

## Part 2: Production Readiness Caveats & Gaps

While the lab demonstrates a working architectural prototype, the following gaps must be resolved before deploying this pattern to production.

### 1. Secrets & Credentials Management
* **Lab Shortcut:** Database passwords are hardcoded in `locals` or `terraform.tfvars`.
* **Production Standard:**
  * Never commit plaintext credentials to source control.
  * Generate passwords dynamically with Terraform (`random_password`) or integrate with **Google Secret Manager**.
  * Ensure `*.tfstate`, `*.tfstate.backup`, and `*.tfvars` containing secrets are excluded in `.gitignore`.

```hcl
# Example: Generate dynamic password
resource "random_password" "db_password" {
  length  = 24
  special = false
}
```

### 2. Decoupling Infrastructure from Applications
* **Lab Shortcut:** Cloud SQL, IAM, and Kubernetes Deployments/Services are provisioned together in a single state file.
* **Production Standard:** Split into two distinct lifecycle tiers to minimize the blast radius:
  1. **Platform / Infra Tier (Terraform):** VPCs, GKE clusters, Cloud SQL, and IAM roles. Managed by the platform/infra team.
  2. **Application Tier (GitOps / Helm):** Deployments, Services, Ingress, and Autoscalers managed via ArgoCD, Flux, or Helm charts.
* **Why it matters:** An application manifest error or rollout failure should never risk modifying, corrupting, or destroying database infrastructure.

### 3. Remote State Backend & Locking
* **Lab Shortcut:** `backend "local"` stores state in a local file (`./terraform.tfstate`).
* **Production Standard:** Use a remote Google Cloud Storage (GCS) backend with state locking and object versioning to enable CI/CD pipelines, prevent concurrent apply conflicts, and avoid catastrophic state loss.

```hcl
terraform {
  backend "gcs" {
    bucket = "my-company-tfstate"
    prefix = "apps/gke-cloudsql"
  }
}
```

### 4. Dependency Timing & API Propagation
* **Lab Shortcut:** Cloud SQL resource created concurrently with API enablement.
* **Production Standard:** GCP API activation (`google_project_service`) is asynchronous in Google's control plane and can take up to 60 seconds to propagate. Always add explicit `depends_on = [google_project_service.enabled_apis]` on dependent cloud resources to avoid intermittent provisioning errors.
* **API Hygiene:** Enable only modern, active APIs (`sqladmin.googleapis.com`), omitting deprecated endpoints (`sql-component.googleapis.com`).

### 5. Kubernetes & Database Hardening
* **Explicit Database Resource:** Do not rely on default databases; explicitly declare `google_sql_database` and inject the database name into the application.
* **Health Probes (Liveness & Readiness):** Add HTTP/TCP readiness probes to application containers. Without readiness checks, LoadBalancers route traffic to pods before the database connection is initialized.
* **Resource Requests & Limits:** GKE Autopilot computes resource provisioning and billing directly from container `requests`. Define CPU and memory requests for both the application and the `cloudsql-proxy` sidecar.
* **High Availability & Disaster Recovery:** Enable `deletion_protection = true`, automated daily backups with point-in-time recovery (PITR), and `availability_type = "REGIONAL"` for production Cloud SQL instances.
