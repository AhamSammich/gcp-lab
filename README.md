# GCP Lab

A scaffolding tool for hands-on Google Cloud Skills labs. Clone or fork it, run `./lab-start` with a new lab name, and it lays out a Terraform working directory under `labs/`, fills in the lab's tasks and variables, and wires up gcloud and Terraform authentication for the lab's student account.

The repository ships only the tool: the helper scripts, the templates they copy from, and the tests. Your lab work stays local. Every lab lives under `labs/<slug>/`, which Git ignores, so finishing a lab never turns your fork into a collection of completed exercises. Archived work goes to `archives/<slug>.zip`, which is ignored too. A fresh clone contains the tool and two empty collections, never anyone's lab solutions or archive payloads.

These are learning exercises, not production-ready infrastructure modules. Use the temporary project and student credentials supplied by the lab. Running them in your own project can incur charges and requires the appropriate permissions, enabled APIs, and billing.

## Prerequisites

- Git and Python 3.10 or newer.
- [Terraform CLI](https://developer.hashicorp.com/terraform/install) and [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) on your `PATH`.
- A running lab with its project ID, assigned region, zone, and student credentials.

Some labs also need tools such as `kubectl`. Check the individual lab instructions before starting.

## Repository layout

```text
gcp-lab/
├── lab-start               # scaffold or refresh a lab
├── lab-reset               # clear one lab's state, tfvars, credentials
├── templates/              # what lab-start copies into a new lab
│   ├── new-standard-lab/   # the standard Terraform skeleton
│   └── new-terraform-lab/   # instructions for lab-supplied Terraform
├── labs/                   # your lab work: local only, gitignored
│   └── <lab-slug>/
└── archives/               # your archived labs: local only, gitignored
    └── <lab-slug>.zip
```

Only the `.gitkeep` placeholders under `labs/` and `archives/` are tracked; everything you put inside those directories stays on your machine. The current checkout tracks the tool alone. Git history is unchanged: it still contains the completed labs this repository carried before the restructure (see "The one-time migration helper" below).

## Quickstart

Clone the repository and run the interactive helper from its root:

```sh
git clone https://github.com/AhamSammich/gcp-lab.git
cd gcp-lab
./lab-start my-new-lab
```

Use a new, kebab-case directory name. The lab is created under `labs/my-new-lab/` no matter which directory you invoke the helper from. Enter the lab URL when prompted, or press Enter to skip fetching it. Supply the project ID and zone from the running lab; the region defaults to the zone's region unless specified separately.

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
cd labs/my-new-lab
# Implement the lab tasks in task1.tf, task2.tf, and so on.
# Review terraform.tfvars and fill in any missing lab-specific values.
terraform fmt
terraform validate
terraform plan -out=lab.tfplan
terraform apply lab.tfplan
```

Review the plan before applying. If initialization or authentication was skipped or failed, complete it first; check the helper's final status rather than assuming everything succeeded.

`lab-start` always uses the standard skeleton for every new lab. There is no selector or alternate mode and no automatic detection: the helper never writes the lab-supplied-Terraform instructions. The instructions template under `templates/new-terraform-lab/` is not wired to the CLI - it exists for future internal plumbing and as manual reference for a lab that supplies its own Terraform repository; such a lab's page names the upstream repository to clone and its own init instructions to follow.

## Restart a lab

When a restarted lab assigns a new project, you can refresh an existing lab's directory from the repository root:

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

Reset recursively deletes `*.tfstate*` and `*.tfvars*` files in the target lab, unsets the current gcloud configuration's project/region/zone, revokes application-default credentials, and revokes the currently active gcloud account. It does not verify that the active account belongs to the lab, so check `gcloud auth list` first. The target must be one managed lab under `labs/`: the helper refuses the repository root, `labs/` itself, paths outside `labs/`, and symlinked targets before changing anything.

State and `.tfvars` files are gitignored. Keep credentials, Terraform plan files, and other sensitive artifacts out of commits too. Browser sessions, rendered page snapshots, and debug logs are stored under `~/.cache/lab-start/`; treat that directory as sensitive. Logs are named `lab-start.log` and `lab-reset.log`.

## The one-time migration helper

This repository used to keep completed labs as tracked directories at its root. A one-time migration (item 002, spec in `.plans/002-directory-restructure.md`) moved those trees out of the current checkout: each previously tracked lab became a verified local zip under `archives/`, and each wholly untracked lab moved under `labs/`. `scripts/migrate_002.py` is that migration; it is not a reusable archive command, and it never runs `gcloud`, `terraform init`, or anything that touches cloud resources or credentials.

What it does, in order: it classifies the lab inventory (a directory with tracked source is a tracked lab however much local work it holds; a wholly untracked directory is a lab only when it shows Terraform lab evidence - anything else at the root is refused rather than guessed), verifies the managed ignore rules and inventories every lab before writing anything, validating each inventory with the same structural and name rules a later restore enforces, checks every destination, archives each tracked lab to `archives/<lab-slug>.zip` plus a `<lab-slug>.meta.json` preservation sidecar, removes exactly the inventoried lab paths from Git's index with scoped, literal-pathspec `git rm --cached` (working files stay on disk, unrelated staged changes stay staged), moves untracked labs under `labs/`, and deletes a source directory only after its complete archive and sidecar re-verify against the original inventory. Before deleting anything it re-inventories the source and stops cold if anything changed while the archive was being written. Name case is data, not decoration: symlink chains that traverse a link through a case-variant spelling of its name, and entry names that collide case-insensitively, are both refused, because a case-insensitive filesystem (the default on macOS and Windows) would resolve them differently than the lexical rules see.

Failure behavior is deliberately boring. Any pre-existing destination stops the run before anything changes, even a valid, byte-identical archive, because accepting one would mean guessing. A failure before retirement removes only the archives the attempt created and leaves every source and the index intact, so you simply retry. A failure later keeps what already verified and reports what moved and what remains; nothing verified is ever deleted. A Ctrl-C is the operator's decision, not a failure: it propagates unwrapped, with the same split - before the index or any source is touched it cleans up the partial attempt so a retry starts clean; after that it keeps the verified archives and prints the progress state to stderr.

Treat `archives/` as sensitive. An archive and its sidecar carry everything the lab contained, including files Git never saw: state, tfvars, untracked task work, and macOS extended attributes. They are owner-only (`0600`) local files. Do not commit, upload, or share them.

Extended attributes are preserved for every entry - files, directories, each lab root, and symlinks' own attributes (read without following the link, so a link and its referent keep distinct attributes). The migration reads every attribute's byte value into the preservation sidecar without writing any; a platform that cannot read an attribute in the required mode aborts the migration rather than silently dropping metadata. Restore then applies the recorded attributes to the extracted tree, which requires compatible attribute-writing support on the target platform and fails loudly there rather than dropping them silently.

To recover a lab from its archive:

```sh
python3 scripts/migrate_002.py restore archives/<lab-slug>.zip /path/to/fresh-directory
```

This rebuilds files, directories, hidden entries, symlink targets, the full permission bits (directories and the lab root included, setuid/setgid/sticky bits among them), and extended attributes, and refuses to write into a destination that already exists. For read-only inspection there are `plan` (classification and collisions) and `preflight` (full per-lab inventory counts); the migration itself runs only with an explicit `--yes`.

## Development

`templates/new-standard-lab/` supplies the skeleton `lab-start` copies into every new lab; `templates/new-terraform-lab/` supplies the lab-supplied-Terraform instructions. The helpers share terminal utilities and have Python `unittest` coverage for layout, authentication, refresh, reset, template plumbing, Git boundaries, and the migration helper.

```sh
python3 -m unittest discover -s tests -v
```

The tests use temporary directories and a stub gcloud command, so they do not require cloud access or change your real credentials.
