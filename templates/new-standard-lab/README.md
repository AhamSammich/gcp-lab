# __LAB_TITLE__

[Go to lab at skills.google](__LAB_URL__)

__LAB_SECTIONS__

## About this lab

This directory is your notebook for the lab: this README holds the
lab's own context, and one `task<N>.md` file per task holds your
notes as you work. The lab page linked above is the source of
truth: its tasks, required outcomes, resource settings, security
constraints, and validation steps are what your work is checked
against. Work each task the way the page prescribes, whatever
tools the page calls for; where the page permits an alternative,
you may take it.

## Prerequisites

- A running lab: start it on the lab page and work it with the
  lab's student account and project, never a personal account.
- Any tool the lab page names, such as gcloud, kubectl, gsutil, or
  bq. Install what the page asks for when it asks.
- Terraform is not assumed here. Follow the page's own procedure;
  if it prescribes a repository or Terraform, that fits this lab
  type too.

## About the generated files

`labkit start <lab-slug> --type standard` (run from the repository
root) created this directory: it rendered this README from the
lab's title, link, and the page's own sections, and wrote one
`task<N>.md` stub per task. You are already inside the lab it
made, so nothing else needs setting up; running the command again
is only for creating another lab.

The start command also offered the gcloud sign-in. If you accepted
it, or an existing sign-in already covers this lab, you are done;
there is no need to sign in a second time. Sign in only if you
passed `--skip-auth` or that step failed, with the lab's student
account, replacing `<project-id>` with the ID from the lab page:

```sh
gcloud auth login --update-adc
gcloud config set project <project-id>
```

## Work the tasks

Each `task<N>.md` names the task it belongs to, matching the lab
page's own task numbering. Open the task on the page, work it as
the page prescribes, and record what you did and observed in the
matching file as you go. The page's required settings and security
constraints apply exactly as written.

## Check your work

Use the lab page's own progress checks after each task, and the
page's final assessment at the end, before you finish. Its
validation steps are the standard your work is measured against.

## Cleanup and preservation

Destroy resources first if needed, while your credentials and any
state files still work: use the lab page's cleanup steps or the
commands you recorded. Reset cannot destroy cloud resources, and it
deletes the files destruction needs, so it cannot come first.

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
