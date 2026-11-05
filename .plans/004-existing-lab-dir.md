# Re-run lab-start on an existing lab dir

**Date**: 2026-11-01
**Revised**: 2026-11-01 - simplified: *use* = lab-reset + re-scaffold; backup machinery dropped
**Status**: Released - shipped and exercised on the real lab (2026-11-01)
**Author**: andre + AI-assisted
**Roadmap**: item 4

## Overview

When `./lab-start` is pointed at a directory that already exists, it
offers two paths: *use* it - run `./lab-reset` on the dir (stale
state, tfvars and credentials are the problem, not history), then
re-scaffold the generated artifacts from the fresh fetch - or make a
new one. Task files and anything else lab-start didn't generate are
never touched. Today it just dies.

## Motivation

### Current State

```python
# lab-start, main() - the whole fetch phase already ran by here
directory = args.directory or ask("New lab directory (kebab-case)",
                                  default=slug, validate=check_slug)
lab_dir = Path.cwd() / directory
if lab_dir.exists():
    sys.exit(f"{RED}{lab_dir} already exists - pick a fresh name{RST}")
```

And `scaffold()` assumes an empty dir:

```python
for path in copied:                                 # backend.tf, providers.tf...
    shutil.copy2(SCAFFOLD_DIR / path.name, path)    # clobbers
task.write_text(TASK_FILE_TEMPLATE...)              # clobbers user work
tfvars.write_text("\n".join(tfvars_lines))          # clobbers
vars_tf.write_text(vars_tf.read_text() + blocks)   # appends blindly
```

This creates problems:

1. **Wasted fetch**: the exit happens *after* the URL fetch and
   browser extraction - a full Chromium run, then death.
2. **Stale lab details with no recovery**: a restarted Qwiklabs lab
   gets new values - new `qwiklabs-gcp-XX-...` project ID, new
   bucket names - while the half-finished dir keeps the old ones in
   `terraform.tfvars` (and worse: a `terraform.tfstate` that
   references a project that no longer exists).
3. **The guard is the only thing preventing data loss**: removing
   the check naively would flatten a 33-line `task1.tf` into a 4-line
   stub. Whatever replaces the check must keep one hard rule.

### Desired State

```
$ ./lab-start landing-zone-challenge --url ...
? landing-zone-challenge/ exists. [u]se it or [n]ew name [u]:
  ... fetch phase ...
Here's the plan:
  directory   landing-zone-challenge/ (existing)
  reset       clears: terraform.tfstate*, terraform.tfvars*, gcloud config + credentials
  rewrites    README.md, variables.tf, terraform.tfvars, providers.tf, backend.tf
  keeps       task1.tf..task4.tf, shared-vpc/
? Proceed? [y]:
```

## Research Findings

### lab-reset already is the teardown half of a refresh

`./lab-reset` (built earlier this session) clears exactly what goes
stale between lab sessions:

| What goes stale on restart       | lab-reset clears it?                    |
| -------------------------------- | --------------------------------------- |
| `*.tfstate*` (references dead project) | yes - recursive delete          |
| `*.tfvars*` (old project/bucket values) | yes                          |
| gcloud project/region/zone config | yes - three `config unset` calls        |
| ADC + active (student) account   | yes - `--quiet` revokes, no prompts     |
| task files, user modules         | **no - not lab-reset's business either** |

**Key finding**: refresh doesn't need new deletion logic - it needs
to *call the tool that already exists*. Reset semantics stay owned
by one file; roadmap item 3 (the `cloud-lab` CLI) will merge them
later anyway.

**Implication**: `use-existing` = invoke `./lab-reset <dir>` (a
subprocess call), then scaffold into the now-clean dir. No `.bak`
machinery, no per-file backup decisions: the old vars are the
problem, not history. git covers the tracked files; task files
aren't touched at all.

### What scaffold must change (and what it must not)

Reading `scaffold()` line by line, refresh-safety is almost free:

| File             | Current write            | Already refresh-safe?                    |
| ---------------- | ------------------------ | ---------------------------------------- |
| backend/providers.tf | copy2 from template  | yes - template-owned, overwrite is right |
| README.md        | template + replace       | yes - rewrite is the point                |
| variables.tf     | copy2 base, then append  | yes - base is overwritten *before* the append, so the result is deterministic (no dedupe needed) |
| terraform.tfvars | generated                | yes - and reset deleted the stale one anyway |
| task<N>.tf       | generated stub           | **no - this is the one fix: write only if missing** |

**Key finding**: one rule - *never write a task file that exists* -
plus `mkdir(exist_ok=True)` is the entire scaffold change.

### What is already re-run-safe

`terraform_init()` is idempotent, and gcloud setup already sits
behind an "Authenticate gcloud now?" prompt - which is where the
post-revoke re-login naturally happens.

## Design Decisions

| Decision              | Choice                        | Rationale                                                                       |
| --------------------- | ----------------------------- | ------------------------------------------------------------------------------- |
| existing-dir response | prompt: [u]se / [n]ew (default use) | the user named the dir on purpose; "new" re-enters the directory prompt    |
| skip flag             | `--use-existing`              | flags-first: every flag passed is a prompt skipped                              |
| `--yes` on existing   | **error**, do not imply        | an unattended run must not silently reset+rewrite; require `--use-existing` too  |
| refresh mechanism     | subprocess call to `./lab-reset` | one owner of reset semantics; its banners/spinners print inline = visible transparency; no circular import (lab-reset already imports lab-start, so logic can't move *into* lab-start without merging them) |
| backups               | none                          | the old vars are the problem; git covers tracked files; the user confirmed they rarely want old vars kept |
| task files            | create missing, never modify  | user work wins even when the stub is bare                                       |
| check placement       | before the fetch phase         | if the positional dir exists, ask use-or-new first - no wasted Chromium run    |
| stale gcloud creds    | revoked by the reset          | the new session needs a new login anyway; the existing auth prompt covers it   |
| stale terraform state | deleted by the reset          | state referencing a dead project is worse than re-creating cheap lab resources  |

## Architecture

```
args.directory exists?
│
├─ no  ──> fetch phase ──> ... (unchanged flow)
│
└─ yes ──> ask [u]se / [n]ew            (--use-existing -> use)
           │
           ├─ new ──> directory prompt ──> fetch phase ──> ... (unchanged)
           │
           └─ use ──> fetch phase ──> preview (reset/rewrite/keep) ──> proceed?
                        │
                        └─ run ./lab-reset <dir>   (its own output prints inline)
                           scaffold(lab_dir, ..., refresh=True)
                             - mkdir(exist_ok=True)
                             - task stubs: write only if missing
                           terraform init ──> auth prompt (re-login happens here)
```

## Implementation Plan

### Phase 1: mode plumbing

- [x] **1.1** `--use-existing` argparse flag; move the exists-check
  pre-fetch when `args.directory` is given; `--yes` + existing dir
  without `--use-existing` exits with a how-to hint
- [x] **1.2** `ask()`-based use/new prompt (default `u`); "new"
  re-enters the directory prompt with a not-exists validator
- [x] **1.3** `refresh` bool through to preview and `scaffold()`

### Phase 2: the two scaffold changes

- [x] **2.1** `mkdir(exist_ok=True)` when refreshing; task stub
  written only when `task{n}.tf` doesn't exist
- [x] **2.2** subprocess call to `REPO_ROOT / "lab-reset"` with the
  lab dir, between the proceed prompt and scaffold; if lab-reset is
  missing/not executable, abort with a hint before any side effects

### Phase 3: preview + tests

- [x] **3.1** preview shows the three lines: *reset* (what lab-reset
  clears), *rewrites* (generated artifacts), *keeps* (existing task
  files + non-generated entries in the dir)
- [x] **3.2** component tests (module-import style, temp dirs, stub
  gcloud on PATH): fixture lab dir with populated task files + a
  user module dir + a fake tfstate; assert task files and module
  byte-identical, tfstate/tfvars deleted then regenerated, README
  rewritten, no task stub overwritten, double-refresh idempotent
- [x] **3.3** CLI tests: `--use-existing` skips the prompt; `--yes`
  without it errors; the "new" path leaves the original dir
  untouched

## Edge Cases

### Refresh on a still-running lab (same project ID)

1. the old session is still valid, resources are deployed, state is
   live
2. reset deletes the state and revokes the credentials anyway
3. accepted: terraform re-creates resources on the next apply, and
   the login prompt re-authenticates - lab resources are cheap and
   disposable. Worth one preview line, not machinery. See Open
   Questions 2.

### The dir exists but isn't a lab

1. `./lab-start some-dir` where the dir has no `*.tf` files
2. refuse the use path ("doesn't look like a lab dir") and offer the
   new-name prompt - a reset on an arbitrary dir is the wrong
   surprise

### lab-reset missing or not executable

1. the repo is cloned without `lab-reset`, or it lost its +x bit
2. abort before any side effects - refresh is defined as
  reset + re-scaffold, not re-scaffold alone

### Hand-edited README.md or variables.tf

1. the user tuned descriptions or notes after the original scaffold
2. refresh rewrites both - the preview said so, git has the tracked
   versions
3. see Open Questions 1 for whether a placeholder-only rule is
   worth it

## Open Questions

All four were settled per the recommendations when implementing:

1. **README refresh policy: always rewrite, or only when placeholders remain?**
   - Options: (a) always rewrite on refresh, (b) rewrite only if
     the README still has `<!-- Add` or `__LAB_` markers
   - **Recommendation**: (a) - "new lab details" is the point, and
     the reset-first model is consistently destructive-or-nothing.
     Revisit only if hand notes in READMEs become a habit.

2. **Reset scope: full (incl. gcloud revoke) or local-only?**
   - A still-running lab session keeps valid credentials; a full
     reset forces a pointless re-login
   - **Recommendation**: full reset, always - one rule, no
     "is the session still live?" detection, and the auth prompt
     makes re-login a non-event.

3. **Invoke lab-reset as a subprocess, or extract a shared function?**
   - Options: (a) `subprocess.run([REPO_ROOT / "lab-reset", dir])`,
     (b) move the reset logic into lab-start, lab-reset becomes a
     thin wrapper (no circular import: lab-reset already imports
     lab-start)
   - **Recommendation**: (a) for this item - zero refactor, and the
     inline banners show the user exactly what reset did. Item 3
     (cloud-lab CLI) merges the two files and settles it for real.

4. **Fewer tasks on the page than task files in the dir**
   - the page says 3, the dir has task1-4.tf
   - **Recommendation**: extra task files stay (the task-protect rule
     covers them); the preview's *keeps* line already lists what
     survives.

## Success Criteria

- [x] `./lab-start landing-zone-challenge --url ...` on the real
  dir: prompt appears, *use* runs lab-reset visibly, then leaves
  `task1-4.tf` and `shared-vpc/` byte-identical, rewrites README.md,
  variables.tf, terraform.tfvars, providers.tf, backend.tf, exits 0
- [x] after the run, tfstate/tfvars are freshly generated (not the
  stale values), gcloud config is set to the new project, and the
  auth prompt completed the re-login
- [x] `--use-existing` skips the prompt; `--yes` alone on an
  existing dir errors with a hint
- [x] "new" path: original dir untouched, fresh dir scaffolded
- [x] missing `lab-reset` aborts before any side effects
- [x] component + CLI tests pass; stubbed terraform/gcloud keep the
  suite hermetic (`python3 -m unittest discover -s tests`)

## References

- `lab-start` - `main()` directory prompt + exists-check (~line
  961), `scaffold()` (~line 720 - needs two changes: exist_ok mkdir,
  task-stub-if-missing), `ask()` for the prompt's shape
- `lab-reset` - the tool being composed in; subprocess target; its
  `--dry-run` could preview the reset inside the plan, if wanted
- `landing-zone-challenge/` - the real refresh target: task1-3.tf
  populated, task4.tf bare stub, live tfvars, `shared-vpc/` module
- `.plans/ROADMAP.md` - item 4; item 3 will merge the two scripts,
  which settles Open Question 3 for good