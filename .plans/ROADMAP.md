# ROADMAP

Living checklist for the lab tooling. To work an item: write its spec
(a markdown file in this directory, named `NNN-<slug>.md` - the number
comes from the item), iterate on the spec until the open questions are
answered, then implement. Specs and this file stay in sync: a checked
Plan box means the spec link actually resolves.

Every item has three stages:

- **Plan** - the spec exists and captures decisions, open questions,
  and acceptance criteria
- **Build** - the code is implemented and tested, while the old way
  still works (the two live side by side during the transition)
- **Release** - the new way is the only way: defaults flipped, old
  paths/scripts/docs cleaned up, the day-to-day workflow uses it

When an item checks all three boxes, move its entry to the *Done*
section at the bottom.

> **Naming:** `cloud-lab` (item 3) is a working title, kept easy to
> change - it appears only in this file and its spec until the Build
> stage, and should stay confined to one constant in the code after
> that. Renaming is a search/replace, not a refactor.

---

## 2. Directory restructure

The repo grows a layout instead of everything at the root:

- rename `terraform-lab-scaffold/` -> `templates/new-standard-lab/`
  (a `git mv`, so history follows the files)
- create `labs/` - unarchived, in-progress lab dirs live here; new
  labs scaffold into it
- create `archives/` - completed labs, zipped and moved out of the way
- create `templates/new-terraform-lab/` - for labs that explicitly
  instruct the use of terraform: they usually involve cloning an
  existing repo rather than writing from scratch, so this template
  carries instructions/scripts, not our skeleton `.tf` files

- [ ] **Plan** - [002-directory-restructure.md](002-directory-restructure.md)
- [ ] **Build** - the layout exists; `lab-start` reads templates from
  `templates/` and scaffolds new labs into `labs/`
- [ ] **Release** - the root-level lab dirs are moved into `labs/`,
  and no code or docs reference the old flat layout

## 3. One CLI: `cloud-lab` (working title)

`lab-start` and `lab-reset` become subcommands of a single CLI, plus
one new command:

- `cloud-lab start` - today's `./lab-start`
- `cloud-lab reset` - today's `./lab-reset`
- `cloud-lab archive` - zip a finished lab and move it to `archives/`
  (assumes the layout from item 2)

- [ ] **Plan** - [003-cloud-lab-cli.md](003-cloud-lab-cli.md)
- [ ] **Build** - the subcommands work; the old entry points still
  exist alongside
- [ ] **Release** - `lab-start`/`lab-reset` are gone or reduced to
  one-line shims; the working title is replaced by the real name here

---

## Done

### 1. Single gcloud login for the student account and ADC

`gcloud_setup()` used to sign in twice - `gcloud auth login` for
the student account, then `gcloud auth application-default
login` for the ADC terraform authenticates with. Now one
`gcloud auth login --update-adc` covers both stores in a single
browser flow, and a cancelled flow bails before any config set
(both-or-neither). The scope caveat the old `--update-adc` docs
carried is dead: the minted ADC token includes cloud-platform,
and a google-provider plan authenticated against the live
project. `lab-reset` is untouched - one flow writes two files,
two revokes still remove both.

- [x] **Plan** - [001-single-gcloud-auth.md](001-single-gcloud-auth.md)
- [x] **Build** - `gcloud_setup()` runs one login, not two
- [x] **Release** - `lab-reset` still revokes both credential
  stores; the ADC scope caveat checked against what terraform
  actually needs (no caveat found, in docs or practice)

### 4. Re-run lab-start on an existing lab dir

`lab-start` used to die on an existing directory, so a half-finished
lab could never pick up new lab details after a Qwiklabs restart
(new project ID, new bucket names, same dir with your work in it).
Now a positional dir that exists offers use-or-new: *use* runs
`./lab-reset` on the dir, then re-scaffolds the generated artifacts
(README, variables.tf, tfvars, providers, backend) from the fresh
fetch, creating only task stubs that are missing. `--use-existing`
skips the prompt; `--yes` alone refuses; a non-lab dir refuses the
use path; task files and everything else lab-start didn't generate
are never touched.

- [x] **Plan** - [004-existing-lab-dir.md](004-existing-lab-dir.md)
- [x] **Build** - the use-or-new prompt, the lab-reset composition,
  task-file protection, and the preview covered by tests
  (`python3 -m unittest discover -s tests`)
- [x] **Release** - exercised on a real unfinished lab
  (`landing-zone-challenge/`); the exit-on-exists branch is gone
