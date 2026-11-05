# GCP Lab

Terraform configurations and notes for hands-on Google Cloud Skills labs, covering networking, security, GKE, Cloud SQL, and Cloud Run. Each lab lives in its own directory with a README linking to the lab instructions.

These are learning exercises, not production-ready infrastructure modules. Use the temporary project and student credentials supplied by the lab. Running them in your own project can incur charges and requires the appropriate permissions, enabled APIs, and billing.

## Prerequisites

- Git and Python 3.10 or newer.
- [Terraform CLI](https://developer.hashicorp.com/terraform/install) and [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) on your `PATH`.
- A running lab with its project ID, assigned region, zone, and student credentials.

Some labs also need tools such as `kubectl`. Check the individual lab instructions before starting.

## Quickstart

Clone the repository and run the interactive helper from its root:

```sh
git clone https://github.com/AhamSammich/gcp-lab.git
cd gcp-lab
./lab-start my-new-lab
```

Use a new, kebab-case directory name. Enter the lab URL when prompted, or press Enter to skip fetching it. Supply the project ID and zone from the running lab; the region defaults to the zone's region unless specified separately.

After you approve the preview, `lab-start` creates the lab files, runs `terraform init`, and offers to authenticate. Sign in with the lab's student account, not your personal account. The helper uses `gcloud auth login --update-adc` so the same login supplies both gcloud and Terraform credentials, then sets gcloud's project, region, and zone.

You can supply known values as flags:

```sh
./lab-start my-new-lab \
  --title "My New Lab" \
  --tasks 3 \
  --project YOUR_LAB_PROJECT_ID \
  --region us-central1 \
  --zone us-central1-a
```

Replace the example region and zone with those assigned by the lab. Flags skip the corresponding questions. Use `--skip-init` or `--skip-auth` to defer those steps, and `./lab-start --help` for all options.

The scaffold contains provider and local-backend configuration, input variables, one `task<N>.tf` stub per task, a README, and a local `terraform.tfvars`. It does not implement the lab resources for you.

```sh
cd my-new-lab
# Implement the lab tasks in task1.tf, task2.tf, and so on.
# Review terraform.tfvars and fill in any missing lab-specific values.
terraform fmt
terraform validate
terraform plan -out=lab.tfplan
terraform apply lab.tfplan
```

Review the plan before applying. If initialization or authentication was skipped or failed, complete it first; check the helper's final status rather than assuming everything succeeded.

### Work on an existing lab

To use a checked-in configuration without regenerating it, enter its directory directly. Read its README and `.tf` files, authenticate, and create a local `terraform.tfvars` matching that lab's variable declarations. Older labs may use `region` and `zone` instead of `compute_region` and `compute_zone`.

For example, [Private Google Access and Cloud NAT](private-google-access-and-cloud-nat/README.md) uses:

```sh
cd private-google-access-and-cloud-nat
gcloud auth login --update-adc
```

Create `terraform.tfvars` with the values assigned by your running lab:

```hcl
project_id = "YOUR_LAB_PROJECT_ID"
region     = "us-central1"
zone       = "us-central1-a"
```

Then run `terraform init`, `terraform validate`, and the plan/apply commands above. Also check any gcloud commands in the lab instructions target the correct project.

### Optional: populate files from a lab page

The helper uses Python's standard library for basic page fetching. To extract content and live values from supported sign-in-protected pages, install Playwright and Chromium:

```sh
python3 -m venv .venv
.venv/bin/pip install playwright
.venv/bin/playwright install chromium
./lab-start --url https://partner.skills.google/course_templates/1573/labs/620245
```

Use your own lab URL. The first browser-assisted fetch opens Chromium for you to sign in with your Skills account. This is separate from the student account used for gcloud. Start the lab on the site first if you want live project and resource values extracted. If fetching fails, enter values manually and fill in the README placeholders.

## Restart a lab

When a restarted lab assigns a new project, you can refresh its directory from the repository root:

```sh
./lab-start my-new-lab --use-existing
```

This runs `lab-reset` before regenerating files. It preserves existing task files and other non-generated source files, but overwrites `README.md`, `providers.tf`, `backend.tf`, `variables.tf`, and `terraform.tfvars`. Back up changes to those files first. Refresh also deletes local state and revokes credentials as described below; it is not a way to reconnect to resources that still need managing.

## Cleanup and credentials

`lab-reset` does not destroy cloud resources. If you need to remove Terraform-managed resources, do so while the project, credentials, and state are still available:

```sh
# From inside the lab directory, review and confirm the destroy plan.
terraform destroy
```

Then, from the repository root, preview the local reset before running it:

```sh
./lab-reset my-new-lab --dry-run
./lab-reset my-new-lab
```

Reset recursively deletes `*.tfstate*` and `*.tfvars*` files in the target directory, unsets the current gcloud configuration's project/region/zone, revokes application-default credentials, and revokes the currently active gcloud account. It does not verify that the active account belongs to the lab. Check `gcloud auth list` first. Never run a reset against the repository root: it would remove state and variable files across labs.

State and `.tfvars` files are gitignored. Keep credentials, Terraform plan files, and other sensitive artifacts out of commits too. Browser sessions, rendered page snapshots, and debug logs are stored under `~/.cache/lab-start/`; treat that directory as sensitive. Logs are named `lab-start.log` and `lab-reset.log`.

## Development

`terraform-lab-scaffold/` supplies the templates used by `lab-start`. The helpers share terminal utilities and have Python `unittest` coverage for authentication and refresh behavior.

```sh
python3 -m unittest discover -s tests -v
```

The tests use temporary directories and a stub gcloud command, so they do not require cloud access or change your real credentials.
