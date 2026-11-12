# labkit

A scaffolding tool for hands-on Google Cloud Skills labs. Clone or fork it, run `./labkit start` with a new lab name, and it lays out a Terraform working directory under `labs/`, fills in the lab's tasks and variables, and wires up gcloud and Terraform authentication for the lab's student account.

The repository ships only the tool, its templates, and tests. Your lab work stays local under ignored `labs/<slug>/` directories. Archived work goes to ignored numbered pairs such as `archives/<slug>--001.zip` and `archives/<slug>--001.meta.json`. A fresh clone contains the tool and two empty collections, never anyone's lab solutions or archive payloads.

These are learning exercises, not production-ready infrastructure modules. Use the temporary project and student credentials supplied by the lab. Running them in your own project can incur charges and requires the appropriate permissions, enabled APIs, and billing.

## Prerequisites

- Git and Python 3.10 or newer.
- [Terraform CLI](https://developer.hashicorp.com/terraform/install) and [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) on your `PATH`.
- A running lab with its project ID, assigned region, zone, and student credentials.

Some labs also need tools such as `kubectl`. Check the individual lab instructions before starting.

## Repository layout

```text
gcp-lab/
├── labkit                  # start, reset, archive, restore: the sole CLI
├── cloud_lab/              # shared Python implementation
├── templates/              # what labkit start copies into a new lab
│   ├── new-standard-lab/   # the standard Terraform skeleton
│   └── new-terraform-lab/   # instructions for lab-supplied Terraform
├── labs/                   # your lab work: local only, gitignored
│   └── <lab-slug>/
└── archives/               # your archived labs: local only, gitignored
    ├── <lab-slug>--001.zip
    └── <lab-slug>--001.meta.json
```

Only the `.gitkeep` placeholders under `labs/` and `archives/` are tracked; everything you put inside those directories stays on your machine. The current checkout tracks the tool alone. Git history is unchanged: it still contains the completed labs this repository carried before the restructure (see "Archive recovery" below).

## Quickstart

Clone the repository and run the interactive helper from its root:

```sh
git clone https://github.com/AhamSammich/gcp-lab.git
cd gcp-lab
./labkit start my-new-lab
```

Use a new, kebab-case directory name. The lab is created under `labs/my-new-lab/` no matter which directory you invoke the helper from. Enter the lab URL when prompted, or press Enter to skip fetching it. Supply the project ID and zone from the running lab; the region defaults to the zone's region unless specified separately.

After you approve the preview, `labkit start` creates the lab files, runs `terraform init`, and offers to authenticate. Sign in with the lab's student account, not your personal account. The helper uses `gcloud auth login --update-adc` so the same login supplies both gcloud and Terraform credentials, then sets gcloud's project, region, and zone.

You can supply known values as flags:

```sh
./labkit start my-new-lab \
  --title "My New Lab" \
  --tasks 3 \
  --project YOUR_LAB_PROJECT_ID \
  --region us-central1 \
  --zone us-central1-a
```

Replace the example region and zone with those assigned by the lab. Flags skip the corresponding questions. Use `--skip-init` or `--skip-auth` to defer those steps, and `./labkit start --help` for all options. Each command has its own help; `./labkit --help` lists the commands.

The scaffold contains provider and local-backend configuration, input variables, one `task<N>.tf` stub per task, a README, and a local `terraform.tfvars`. It does not implement the lab resources for you.

```sh
cd labs/my-new-lab
# Implement the lab tasks in task1.tf, task2.tf, and so on.
# Review terraform.tfvars and fill in any missing lab-specific values.
terraform fmt
terraform validate
terraform plan -out=lab.tfplan
terraform apply lab.tfplan
```

Review the plan before applying. If initialization or authentication was skipped or failed, complete it first; check the helper's final status rather than assuming everything succeeded.

`labkit start` always uses the standard skeleton for every new lab. There is no selector or alternate mode and no automatic detection: the helper never writes the lab-supplied-Terraform instructions. The instructions template under `templates/new-terraform-lab/` is not wired to the CLI; it exists for future internal plumbing and as manual reference for a lab that supplies its own Terraform repository. Such a lab's page names the upstream repository to clone and its own init instructions to follow.

## Restart a lab

When a restarted lab assigns a new project, you can refresh an existing lab's directory from the repository root:

```sh
./labkit start my-new-lab --use-existing
```

This runs the reset workflow before regenerating files. It preserves existing task files and other non-generated source files, but overwrites `README.md`, `providers.tf`, `backend.tf`, `variables.tf`, and `terraform.tfvars`. Back up changes to those files first. Refresh also deletes local state and revokes credentials as described below; it is not a way to reconnect to resources that still need managing.

## Cleanup and credentials

`labkit reset` does not destroy cloud resources. If you need to remove Terraform-managed resources, do so while the project, credentials, and state are still available:

```sh
# From inside the lab directory, review and confirm the destroy plan.
terraform destroy
```

Then, from the repository root, preview the local reset before running it:

```sh
./labkit reset my-new-lab --dry-run
./labkit reset my-new-lab
```

Reset recursively deletes `*.tfstate*` and `*.tfvars*` files in the target lab, unsets the current gcloud configuration's project/region/zone, revokes application-default credentials, and revokes the currently active gcloud account. It does not verify that the active account belongs to the lab, so check `gcloud auth list` first. The target must be one managed lab under `labs/`: the helper refuses the repository root, `labs/` itself, paths outside `labs/`, and symlinked targets before changing anything.

State and `.tfvars` files are gitignored. Keep credentials, Terraform plan files, and other sensitive artifacts out of commits too. Browser sessions, rendered page snapshots, and debug logs are stored under `~/.cache/lab-start/`; treat that directory as sensitive. Logs are named `lab-start.log` and `lab-reset.log`. These historical cache and log paths intentionally stay unchanged after the command rename.

## Finish a lab

Destroy cloud resources first if needed, while the state and credentials are still available. Run `terraform destroy` from the lab directory and confirm its plan. Then, from the repository root, archive before clearing credentials so the pair retains the full lab:

```sh
./labkit archive my-new-lab --dry-run
./labkit archive my-new-lab
./labkit reset --credentials-only --dry-run
./labkit reset --credentials-only
```

Archive verifies the complete numbered zip/sidecar pair before removing the source lab. It does not destroy resources and does not sign you out. Credential-only reset needs no lab, touches no lab files, and exits nonzero if any credential-cleanup step fails or cannot be confirmed. Ordinary reset instead deletes local state and tfvars and keeps its best-effort credential cleanup; it is not the finishing workflow when you want to preserve those files.

Use a single writer. Do not run concurrent commands or edit a lab during archive or restore. These operations are not crash-atomic transactions; after a failure, read the reported recovery and remaining-source paths before acting. Recovery is manual, not automatic resume or merge.

## Archive recovery

This repository used to keep completed labs as tracked directories at its root. The completed one-time migration (item 002, spec in `.plans/002-directory-restructure.md`) archived previously tracked labs under `archives/` and moved wholly untracked labs under `labs/`. That migration executable is retired. Its unnumbered version-1 archive pairs remain recoverable, as do the numbered pairs created by `labkit archive`.

Treat `archives/` as sensitive. An archive and its sidecar carry the full lab tree, including hidden and ignored files, credentials, state, tfvars, plan files, nested repositories, untracked task work, and extended attributes. Both artifacts are gitignored, owner-only (`0600`) local files. Do not commit, upload, or share them.

Extended attributes are preserved for every entry: files, directories, each lab root, and symlinks' own attributes (read without following the link, so a link and its referent keep distinct attributes). Archive reads every attribute's byte value into the preservation sidecar without writing any; a platform that cannot read an attribute in the required mode aborts the archive rather than silently dropping metadata. Restore then applies the recorded attributes to the extracted tree, which requires compatible attribute-writing support on the target platform and fails loudly there rather than dropping them silently.

To recover a lab from its archive:

```sh
./labkit restore archives/<lab-slug>.zip /path/to/fresh-directory
# For a numbered pair, use its exact filename instead:
./labkit restore archives/<lab-slug>--001.zip /path/to/another-fresh-directory
```

Keep the matching `.meta.json` beside the zip. Restore validates the complete pair before extraction and refuses any existing destination, including dangling links. It rebuilds files, directories, hidden entries, safe relative symlink targets, full permission bits (including root and directory modes and setuid/setgid/sticky bits), and extended attributes. It does not preserve ownership, timestamps, or hard-link identity. On failure or Ctrl-C, it removes only its own partial destination and reports any remnant; the input pair stays untouched. Recovery is manual into a fresh destination, never a merge into an existing lab.

The former standalone restore script is retired; use `./labkit restore` instead. Run `./labkit restore --help` for arguments. Exit codes are 0 for a verified restore, 1 for refusal, 2 for invalid arguments, and 130 for Ctrl-C. Restore does not invoke cloud tools or change credentials.

## Development

`templates/new-standard-lab/` supplies the skeleton `labkit start` copies into every new lab; `templates/new-terraform-lab/` supplies the lab-supplied-Terraform instructions. The workflows share terminal utilities and have Python `unittest` coverage for layout, authentication, refresh, reset, template plumbing, Git boundaries, archive preservation, and old/new archive recovery.

```sh
python3 -m unittest discover -s tests -v
```

The tests use temporary directories and a stub gcloud command, so they do not require cloud access or change your real credentials.
