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

## 3. One CLI: `cloud-lab` (working title)

`lab-start` and `lab-reset` become subcommands of a single CLI, plus
one new command:

- `cloud-lab start` - today's `./lab-start`
- `cloud-lab reset` - today's `./lab-reset`
- `cloud-lab archive` - zip a finished lab and move it to `archives/`
  (assumes the layout from item 2; labs and archives remain local-only
  and gitignored)

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

### 2. Directory restructure

The repo became a scaffolding tool for users to clone or fork, rather
than a tracked collection of completed labs. The layout now is:

- `terraform-lab-scaffold/` renamed to `templates/new-standard-lab/`
  (a `git mv`, so history followed the files)
- `labs/` - unarchived, in-progress lab dirs live here; new labs
  scaffold into it; contents are local-only and gitignored
- `archives/` - completed labs, zipped out of the way during Release:
  all tracked labs were archived here; wholly untracked labs moved
  into `labs/`, including their local working files. Archives are
  local-only and gitignored, not committed
- all actual lab content removed from Git tracking, preserving local
  data. Tooling, reusable templates, documentation, synthetic tests,
  and directory placeholders stayed tracked; Git history was not
  rewritten
- `templates/new-terraform-lab/` - for labs that explicitly instruct
  the use of terraform: they usually involve cloning an existing repo
  rather than writing from scratch, so this template carries
  instructions, not our skeleton `.tf` files (internal plumbing only;
  defaults to `standard`, with no `--template` flag or selection
  prompt)

For this item, no legacy-layout support was built. This was an
exception to the general side-by-side transition rule above.

- [x] **Plan** - [002-directory-restructure.md](002-directory-restructure.md)
- [x] **Build** - the layout exists; `lab-start` reads templates from
  `templates/` and scaffolds new labs into `labs/` (evidence recorded
  in the spec's Phase 2 checklist; the migration and fresh-clone
  contract tests stayed red on purpose until Release)
- [x] **Release** - actual migration run 2026-11-07 and independently
  verified: 15 tracked labs archived to owner-only zips + sidecars
  under `archives/` (every payload hash, mode, symlink target, and
  xattr matched the original inventory; all 15 restored and re-verified
  in disposable directories), 4 untracked labs moved under `labs/`
  byte-identical, exactly the 67 tracked lab files staged for deletion,
  `git ls-files` carries only tool/templates/docs/tests/placeholders,
  and the fresh-clone test went green against the released HEAD
  (130/130; disposable-clone smoke 30/30)

Final acceptance: independent `GPT-6 Astra#xhigh` adversarial review approved
the remediated committed code with no remaining P0/P1 findings. The expanded
suite runs 176 tests: 175 pass and one filesystem-dependent test skips.
See [the implementation summary](002-implementation-summary.md) for review,
preservation, and commit details.
