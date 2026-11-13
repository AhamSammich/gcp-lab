# __LAB_TITLE__

[Go to lab at skills.google](__LAB_URL__)

__LAB_SECTIONS__

## About this lab

You write the Terraform for this lab yourself: the configuration,
resource by resource, task by task. The lab page stays the source
of truth. Its objectives, required resource settings, security
constraints, and validation steps all still apply; what changes is
that the design is yours to work out. Using another tool where it
genuinely helps (the console, gcloud commands) is fine. The outcome
is the point, not the tool.

If your lab page supplies its own Terraform repository, you can
still solve the lab here with your own configuration; the page's
outcomes and requirements apply to your solution either way.
Following the supplied repository is the standard procedure when
you choose it. Authoring your own is what this template is for.

## Prerequisites

- The [Terraform CLI](https://developer.hashicorp.com/terraform/install)
  on your PATH.
- The [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) on
  your PATH.
- A running lab: start it on the lab page and note its student
  account, project ID, region, and zone.

## Set up the lab directory

You are reading this inside the lab it describes: `./labkit start
<lab-slug> --type terraform` (run from the repository root) created
this directory from this template, rendered this README's title,
link, and the page's own overview and objectives, wrote one
numbered `task<N>.tf` stub per task, generated
`terraform.tfvars` from the running lab's project, region, and
zone, and recorded the lab's type. A pass-through URL (`--url`)
pre-fills even more from the lab page. Nothing else needs setting
up, and running the command again is only for creating another lab.

Unless you passed `--skip-init`, the command also ran `terraform
init` here, and it offered the gcloud sign-in. If you accepted
that sign-in, or an existing one already covers this lab, you are
done; there is no need to sign in a second time. Complete whichever
step you skipped or that failed; the appendix's final block shows
the commands for both.

Prefer to copy the files by hand instead? The appendix at the end
of this README documents that path; it creates the same layout.

## Work the tasks

Each task has its own `task<N>.tf` stub, numbered and titled from
the lab page. Work one task at a time: read its instructions on
the lab page (the number and title in the stub are a label for
finding them, not the instructions), decide what the task needs,
and write the configuration in the stub's workspace. Add other
files whenever your lab needs them.

The stub carries the questions worth asking per task: which
resources create what the page asks for, which data sources read
what already exists, which required settings the page names, and
what depends on what. It names no resource types on purpose; check
the provider documentation for the ones your lab calls for.

## Check your work

The lab page owns the checks: run its progress check for a task
before starting the next one, and when a check fails, reread its
message. It names the requirement that isn't met yet.

Keep the configuration tidy and structurally sound as you go:

```sh
terraform fmt
terraform validate
```

Then look before you change anything:

```sh
terraform plan
```

Read the plan. It is the honest list of what your configuration is
about to create, change, or destroy. Apply it when it matches the
task:

```sh
terraform apply
```

## Cleanup

Destroy what you created first, while the project, your Terraform
state, and your credentials still work: run `terraform destroy`
from this lab directory and confirm its plan. Reset deletes the
state and revokes the credentials that destruction needs, so it
cannot come first.

`../../labkit reset` (from inside this lab directory) is local
cleanup only: it deletes every `*.tfstate*` and `*.tfvars*` file
anywhere under the lab directory, unsets gcloud's
project/region/zone, and revokes the machine-wide
application-default credentials plus whichever gcloud account is
active at the moment. It does not destroy cloud resources, and it
does not touch your task files. It never checks that the active
account is the lab's, so run `gcloud auth list` first; resetting
with an unrelated account signed in signs that account out.

To keep the full lab instead, return to the repository root and
archive before clearing credentials: preview with
`./labkit archive <lab-slug> --dry-run`, run
`./labkit archive <lab-slug>`, then
`./labkit reset --credentials-only`. The ignored archive pair
includes hidden and sensitive files. See the tool README for manual
recovery into a fresh destination and single-writer limits.

## Appendix: set up by hand

labkit can also scaffold this from nothing, but copying the
template by hand works too. Replace `<lab-slug>` with your lab's
kebab-case name and paste the block below from the repository root
as one piece:

```sh
mkdir "labs/<lab-slug>" &&
cp templates/new-terraform-lab/README.md templates/new-terraform-lab/task1.tf templates/new-terraform-lab/backend.tf templates/new-terraform-lab/providers.tf templates/new-terraform-lab/variables.tf "labs/<lab-slug>/" &&
cd "labs/<lab-slug>"
```

The chain stops at the first failing step. A directory that
already exists fails the `mkdir`, so the copy never runs, your
existing work is never overwritten, and you stay outside a lab
that wasn't set up. The quotes keep a name with spaces one name.

Then fill in this README's three markers by hand: the title at the
top becomes the lab's title, the link below it becomes its URL, and
the sections block becomes the page's own overview and objectives,
keeping the page's required settings, security constraints, and
validation notes alongside them.

No `terraform.tfvars` ships with this template, and the manual
path does not generate one: lab values stay out of the repository.
Create the file yourself in this directory from the running lab's
project, region, and zone, like this:

```hcl
project_id     = "<project-id>"
compute_region = "<region>"
compute_zone   = "<zone>"
```

The three names match the variables in `variables.tf`. The file is
gitignored, so it stays local to your machine.

Last, copy `task1.tf` once per task (`task2.tf`, `task3.tf`, and so
on), renumbering the header line in each, sign in with the lab's
student account (never a personal one), and initialize Terraform:

```sh
gcloud auth login --update-adc
gcloud config set project <project-id>
terraform init
```

One sign-in covers both gcloud itself and the application-default
credentials the Google provider authenticates with.
