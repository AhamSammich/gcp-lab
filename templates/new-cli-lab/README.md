# __LAB_TITLE__

[Go to lab at skills.google](__LAB_URL__)

__LAB_SECTIONS__

## About this lab

You work this lab on the command line: you write and run each
command yourself, one at a time, and check your work as you go. The
lab page stays the source of truth. Its objectives, required
resource settings, security constraints, and validation steps all
still apply; what changes is that the procedure is yours to write.
Using another tool where it genuinely helps (the console, an
editor, Terraform if you want it) is fine. The outcome is the point,
not the tool.

## Prerequisites

- The [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) on your PATH.
- A running lab: start it on the lab page and note its student
  account and project ID.
- Any other CLI the lab itself names, such as kubectl, gsutil, or
  bq. Install those when the lab asks for them.
- Terraform is not a prerequisite for this lab type. Skip it unless
  your lab asks for it.

## Set up the lab directory

You are reading this inside the lab it describes: `./labkit start
<lab-slug> --type cli` (run from the repository root) created this
directory from this template, rendered this README's title, link,
and the page's own overview and objectives, wrote one numbered
`task<N>.sh` stub per task, and recorded the lab's type. A
pass-through URL (`--url`) pre-fills even more from the lab page.
Nothing else needs setting up, and running the command again is
only for creating another lab.

The start command also offered the gcloud sign-in. If you accepted
it, or an existing sign-in already covers this lab, there is
nothing more to do here. Sign in only if you passed `--skip-auth`
or that step failed, with the lab's student account (never a
personal one), replacing `<project-id>` with the ID from the lab
page:

```sh
gcloud auth login --update-adc
gcloud config set project <project-id>
```

Prefer to copy the files by hand instead? The appendix at the end
of this README documents that path; it creates the same layout.

## Work the tasks

Each task has its own `task<N>.sh` stub, numbered and titled from
the lab page. Work one task at a time: read its instructions on
the lab page (the number and title in the stub are a label for
finding them, not the instructions), run one command at a time, and
record each command in the task's file as you go. Running the file
again repeats what you recorded. Add other files whenever your lab
needs them.

Find commands instead of guessing them: `gcloud --help` lists the
command groups, `gcloud <group> --help` opens one, and most commands
explain their flags with `--help`. The stub repeats this and leaves
the workspace for your commands.

## Check your work

The lab page owns the checks: run its progress check for a task
before starting the next one, and when a check fails, reread its
message. It names the requirement that isn't met yet. Reading state
back helps too: the same CLI that creates a resource can usually
list and describe it, so you can confirm what your commands actually
did instead of assuming.

## Cleanup

Destroy what you created first, while the project, any Terraform
state, and your credentials still work: use the lab page's cleanup
steps or the commands you recorded, and `terraform destroy` for
anything you managed with Terraform. Reset deletes the state and
revokes the credentials that destruction needs, so it cannot come
first.

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
cp templates/new-cli-lab/README.md templates/new-cli-lab/task1.sh "labs/<lab-slug>/" &&
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

Last, copy `task1.sh` once per task (`task2.sh`, `task3.sh`, and so
on), renumbering the header line in each, and sign in with the
lab's student account (never a personal one), replacing
`<project-id>` with the ID from the lab page:

```sh
gcloud auth login
gcloud config set project <project-id>
```
