# Directory restructure

**Date**: 2026-11-05
**Revised**: 2026-11-06; scaffolding-only repository, local ignored labs and archives
**Released**: 2026-11-07
**Status**: Released
**Author**: AI-assisted
**Roadmap**: item 2

## Overview

Make this a scaffolding tool that users clone or fork to complete their own
labs, not a versioned collection of completed exercises. Keep helpers,
reusable templates, documentation, and synthetic tests tracked; keep lab
work and archives local and gitignored. Archive the existing tracked labs,
remove all lab content from Git's index, and move untracked labs under
`labs/`. Item 3 still owns the reusable archive command; this item includes
only the one-time migration.

## Motivation

### Current state

The scripts live beside the lab directories. Template lookup is already
anchored to the script, but lab lookup depends on the caller's directory:

```python
# lab-start
REPO_ROOT = Path(__file__).resolve().parent
SCAFFOLD_DIR = REPO_ROOT / "terraform-lab-scaffold"
candidate = Path.cwd() / directory
lab_dir = Path.cwd() / directory

# lab-reset
lab_dir = Path(args.directory).resolve() if args.directory else Path.cwd()
```

This creates four problems:

1. Templates and lab work share the root, with no place reserved for archives.
2. Changing only `SCAFFOLD_DIR` would leave prompts, collision checks,
   refresh, reset examples, and next-step instructions using the flat layout.
3. The current scaffold always writes our Terraform configuration. A lab
   that supplies its own Terraform repository needs a different starting point.
4. Cloning or forking the tool also brings completed lab content. New user
   work should not become part of the tool's tracked source by default.

### Desired state

```text
gcp-lab/
├── lab-start
├── lab-reset
├── templates/
│   ├── new-standard-lab/       # existing four template files, moved
│   └── new-terraform-lab/      # instructions for lab-supplied Terraform
├── labs/
│   └── <lab-slug>/            # local and ignored; never tracked by default
├── archives/
│   └── <lab-slug>.zip         # local and ignored; never committed
├── tests/
└── .plans/
```

After release, `./lab-start my-lab` creates `labs/my-lab/`, regardless
of the caller's working directory. `./lab-reset my-lab` names that same
lab; running reset without an argument inside a lab still targets the
current directory.

Template selection is internal and defaults to `standard`. There is no
`--template` flag, selection prompt, or automatic page-based selection.
There is no legacy layout mode or root-level lab fallback.

Only `.gitkeep` placeholders under `labs/` and `archives/` remain tracked.
A fresh clone contains the tool, not lab solutions or archive payloads.
This changes the current tracked tree, not existing Git history; no history
rewrite or remote purge is included.

## Research findings

Research was local: the helpers, templates, tests, tracked-file inventory,
ignore rules, and lab-relative Terraform references were inspected. No
external framework or new dependency is needed for this migration.

| Area | Finding | Implication |
| --- | --- | --- |
| Standard template | Four files: `README.md`, `backend.tf`, `providers.tf`, `variables.tf`; task files and tfvars are generated | Move the existing template with `git mv`; keep its content unchanged initially |
| Path consumers | `check_fresh_slug()`, positional and prompted directory checks, `use_or_new()`, preview, and final `cd` output depend on the old layout | Resolve a target once and retain its full path; do not reconstruct it from `.name` |
| Refresh | Calls the root `lab-reset` with the target path, then rewrites generated files while preserving tasks and user modules | Keep the absolute target throughout; preserve item 4's guarantees |
| Reset | Recursively deletes `*.tfstate*` and `*.tfvars*`; no argument currently accepts any working directory | Reject repository and collection roots before deletion or gcloud changes |
| Tests | CLI tests execute the real scripts from temporary working directories; component tests hard-code the old template path | Relocate a minimal repository fixture so the new root-anchored behavior cannot write into the real checkout |
| Terraform paths | Inspected local backends use `./terraform.tfstate`; local modules use paths inside their lab | Move whole lab trees; inspect cached module metadata and generated files before assuming no follow-up initialization is needed |
| Local work | Four whole lab directories are untracked at planning time; tracked labs can also contain ignored local state, tfvars, caches, and untracked tasks | Inventory disk contents as well as Git; untrack lab source without discarding local work |
| Ignore rules | Terraform state/tfvars/cache patterns already apply below nested directories; there is no archive rule | Ignore all lab/archive contents, not just secrets; existing tracked content requires explicit index removal |

Baseline: `python3 -m unittest discover -s tests` passed all 22 tests on
2026-11-05. These tests cover authentication and refresh, not the new layout.

The untracked lab directories observed were
`design-secure-landing-zones-and-architecture/`,
`develop-serverless-apps-on-cloud-run/`, `private-google-access-for-vsc/`,
and `vpc-network-peering/`. The user confirmed on 2026-11-06 that untracked
labs move under `labs/` and tracked labs are archived. Recheck the inventory
at Release; this planning task does not perform those operations.

## Design decisions

The user settled template exposure, compatibility, migration destinations,
and the scaffolding-only repository policy on 2026-11-06. Safety and
data-preservation rules still apply to index removal, moves, and archives.

| Decision | Choice | Rationale |
| --- | --- | --- |
| Repository purpose | Track the scaffolding tool, reusable templates, documentation, and synthetic tests; no actual lab content | Users clone or fork the tool to do their own work |
| Layout ownership | Paths anchored to `REPO_ROOT`; one small path-resolution boundary shared by both helpers | A change of working directory must not change which lab is refreshed or reset |
| Standard behavior | Default to `templates/new-standard-lab/`; preserve current generated content and flags | This is a directory migration, not a Terraform scaffold redesign |
| Template selection | Internal template identity/path plumbing with default `standard`; no public selector | Prepare for another template without exposing an unfinished workflow |
| Terraform-supplied workflow | Create the instructions template; defer its end-to-end CLI workflow | The upstream repository and working subdirectory vary by lab; the CLI remains standard-only |
| Compatibility | No legacy flag, old template alias, or root-lab fallback | The user explicitly waived side-by-side layout support for this item |
| Duplicate names | Bare names refer only to `labs/<slug>`; explicit old-root targets fail | Root-level trees must never shadow managed labs |
| Release migration | Archive every tracked lab; move wholly untracked lab trees into `labs/` | Separate completed tracked work from the user's current local labs |
| Mixed lab contents | A lab with tracked source is a tracked lab even if it contains untracked tasks or ignored state | Classify whole lab directories, not individual files; preserve all local additions |
| Archives | One verified local-only zip per previously tracked lab; never commit payloads | User confirmed archives remain gitignored for now |
| Lab tracking | Ignore `labs/` contents and explicitly remove every currently tracked lab path from the index | `.gitignore` does not untrack files already in Git |
| Tracked placeholders | Ignore `/labs/*` and `/archives/*`, with exceptions for each `.gitkeep` | Preserve the layout in fresh clones without tracking user work |
| History | No rewrite, purge, or force push | Removing content from the current index does not erase historical commits |
| Dependencies | Keep the current stdlib-only baseline and optional browser fetch | No packaging or CLI-framework migration is needed |

### Target resolution contract

For start, a bare slug maps to `REPO_ROOT / "labs" / slug`. Prompted names,
page-derived defaults, and the new-name collision validator use the same
mapping. Existing standard labs retain use-or-new, explicit `--use-existing`,
and the refusal of `--yes` alone. A missing target with `--use-existing`
must fail, not silently scaffold a replacement after a typo.

Explicit paths resolve relative to the caller, or as absolute paths, and
must identify a direct lab directory under `labs/` from Build onward.
Reject old-root targets, traversal,
symlink escapes, files, and collection roots before fetch/reset/write.
Do not allow new labs inside other labs, templates, or archives.

For reset, a bare slug resolves to the managed lab; an explicit path
must identify an individual managed lab under `labs/`. No argument retains
the current-directory behavior inside a managed lab, not an old root-level
lab. Reject the repository root,
`labs/`, `templates/`, `archives/`, and their ancestors before any file or
credential changes. This is a narrow collection-root safety guard, not a
redesign of recursive cleanup within a valid lab or global gcloud handling.

Previews show the resolved target. Next-step `cd` commands must work from
the invocation directory, including when it is outside the repository;
quote paths that need quoting. Refresh passes that same resolved target
to `lab-reset`, never just its basename.

### Template plumbing and deferred Terraform workflow

Keep template identity, directory lookup, and generated-file policy behind
an internal boundary rather than scattering the standard template path
through the script. The CLI always selects `standard` in this item. Tests
may exercise internal template resolution without adding a public flag.

`templates/new-terraform-lab/README.md` uses the same title, URL, and
extracted-section placeholders as the standard README. It explains how to
authenticate with the student account, clone the repository named by the
lab into the working lab directory, select the instructed subdirectory,
and follow that repository's init/variables/backend instructions. Do not
invent an upstream URL, branch, or working directory.

Do not add a setup script unless implementation research identifies a
concrete reusable step. Instructions alone satisfy the roadmap's intent.
There is no user-facing Terraform mode to implement or document yet.
Task-count prompting, initialization, README-only generation, and safe
refresh of upstream repositories must be specified when selection is
eventually exposed. Do not implement these deferred branches now or infer
a template from files in an existing lab.

### One-time migration contract

Build an explicit inventory of lab directories from tracked paths and the
working tree. Exclude tooling, templates, tests, virtual environments, and
metadata. A directory containing tracked lab source goes to
`archives/<slug>.zip`; a wholly untracked lab goes to `labs/<slug>/`.
Untracked and ignored files inside a tracked lab belong to that lab's
preservation inventory, not to the wholly untracked-lab category.

Check every destination before changing any source. Create archives without
overwriting existing files, reopen them, and verify entry paths and content
hashes against the agreed inventory before retiring source directories.
Preserve the complete recoverable local lab tree, including hidden files,
uncommitted edits, untracked tasks, and ignored state/tfvars. Do not silently
drop contents because Git ignores them. If an entry cannot be represented
faithfully in a zip, preserve it separately and verify that copy before
retiring the source. Treat local archives as sensitive and do not upload,
commit, or share them. Git history alone does not preserve local-only work.

Before any lab becomes untracked, enable the managed-directory ignore
rules. Remove only inventoried lab paths from Git's index using scoped
`git rm --cached` operations; this step must leave working files intact.
Do not force removal if staged changes conflict with the inventory. Never
use a whole-repository untrack/re-add operation. Archive verification and
source retirement are separate from untracking: do not substitute ordinary
`git rm` for index-only removal or delete the only local copy.

After migration, the index must contain no actual lab source, notes,
lab-specific scripts, generated artifacts, or zip payloads. Preserve tool
scripts, templates, project documentation, and synthetic tests. Do not
force-add ignored user content. Historical lab references in planning docs
may remain as history, but active documentation cannot depend on a local
lab or archive being available to someone cloning the repository.

## Architecture

```text
args / prompted slug
         │
         ▼
resolve managed target + safety checks
         │
         ├── start: internal default standard → fetch / collect → preview → confirm
         │              └── reset if refresh → scaffold → init / auth
         │
         └── reset: resolved individual lab → existing cleanup / revoke flow

Release: inventory → collision checks
                        ├── tracked labs → verified local zips → untrack → retire sources
                        └── untracked labs → ignored labs/ → verify whole-tree moves
         final index: tool + templates + docs + synthetic tests only
```

## Implementation plan

### Phase 1: settle contracts and add failing tests

- [x] Settle template exposure, compatibility, migration destinations, and
  the scaffolding-only/local-archive policy.
- [x] Extend `tests/util.py` with an isolated repository fixture containing
  the helpers and templates. Keep temporary HOME, stub gcloud, and stub or
  skipped Terraform; relocate every CLI test that would otherwise write
  into the actual checkout's `labs/`.
- [x] Add failing layout tests for script-root-relative templates, bare
  slugs from different working directories, prompted/new-name collisions,
  explicit paths, missing refresh targets, old-root target refusal,
  traversal, and symlink escapes. Assert target contents and untouched
  sibling trees, not only output text.
- [x] Add failing reset tests for slug/explicit/current-directory targets
  and collection-root refusal. Assert no gcloud calls and byte-identical
  state/tfvars after a rejected operation, even with `--dry-run` omitted.
- [x] Add failing template-plumbing tests: internal default resolves to the
  relocated standard template, standard output stays equivalent, both
  template directories exist, and no selector flag or prompt is exposed.
- [x] Add failing migration tests with disposable tracked/untracked lab
  fixtures: classification of mixed contents, destination collisions,
  archive reopening and hash verification, unsafe archive-entry refusal,
  and preservation of local-only work on success or interruption.
- [x] Add failing Git-boundary tests in disposable repositories: new lab
  files and zip payloads are ignored, placeholders remain trackable, scoped
  index removal preserves working files, and unrelated tracked tool files
  and staged changes are untouched. Fresh-clone tests must not require
  completed labs or local archives.
- [x] Run the new tests to establish the expected failures; do not weaken
  existing authentication or task-preservation assertions to make them pass.
- [x] Remediate the P1 adversarial review findings on the initial Phase 1
  contract snapshot: pin the staged lab deletions that scoped
  `git rm --cached` correctly produces next to the unrelated staged tool
  changes (exact `--name-status` set plus unchanged blob ids/modes); make
  every pre-existing archive destination collide - even a valid,
  inventory-verified zip - with failed-attempt cleanup replacing
  resume-by-acceptance in the stub contract; and add the missing
  target-contract coverage: valid start targets via explicit relative and
  absolute paths, a prompted name with no positional argument, a
  page-derived default that collides (defaults must validate like typed
  answers), managed `labs/<slug>` and `labs/` collection-root symlink
  escapes for both helpers, reset traversal refusal (dry-run and not)
  with byte-identical victims and no gcloud, sibling-lab isolation on a
  valid reset, next-step `cd` resolution (shlex-parsed, from a
  spaced repo path outside the repo), and template safety (missing
  standard template aborts before reset or partial scaffold with no
  gcloud; a missing unrelated terraform template cannot break standard
  mode; the instructions template carries only placeholders and
  workflow guidance; standard parity includes variables.tf and the
  fully rendered README).

  Red baseline, revised after remediation (2026-11-06,
  `python3 -m unittest discover -s tests`):
  79 tests, 47 expected failures/errors, 32 green - the 22 pre-existing
  auth/refresh tests (relocated onto the isolated fixture) plus 10
  contract pins that already hold today (file-target refusal, absolute
  old-root safe-abort, explicit-path and current-dir reset of a managed
  lab, no `--template` flag, placeholders trackable, explicit relative
  and absolute start paths for a new managed lab, relative explicit
  reset path, and managed-symlink reset refusal). The migration API is
  pinned by `scripts/migrate_002.py` (contract-only stub; implemented
  in Phase 3) and `tests/test_migration.py`.

### Phase 2: Build the new layout directly

- [x] Use `git mv terraform-lab-scaffold templates/new-standard-lab`
  (blob ids and file bytes unchanged through the move). Add the
  Terraform instructions template, tracked `labs/.gitkeep` and
  `archives/.gitkeep`. Ignore `/labs/*` and `/archives/*`, except those
  placeholders, while preserving existing sensitive-file exclusions.
- [x] Implement the target resolver and safety boundary
  (`lab-start.resolve_lab_target`, consumed by both helpers), then update
  directory checks, template lookup, refresh invocation, preview, summary,
  and next steps to consume resolved targets consistently.
- [x] Implement internal template lookup with default `standard`
  (`DEFAULT_TEMPLATE`/`TEMPLATE_IDS`/`template_dir`); create
  the Terraform instructions template without exposing a flag, prompt,
  auto-detection, or alternate end-to-end workflow.
- [x] Remove flat-layout assumptions directly. Do not add compatibility
  flags, old-root fallbacks, duplicate templates, or old-path symlinks.
- [x] Update component tests to use the relocated standard template and
  keep meaningful byte-for-byte refresh, task/module preservation, and
  single-login checks. Keep fixtures synthetic and independent of actual
  lab trees. Update helper documentation to the new layout.
- [x] Run the full suite. Mark Build only when new labs land under `labs/`,
  standard behavior is preserved, internal plumbing exists, and old-root
  targets cannot reset or overwrite the old labs awaiting migration.

  Build evidence (2026-11-06, `wip(002-phase2:` commit on
  `feat/002-directory-restructure`): `python3 -m unittest discover -s
  tests` ran 80 tests - 70 green, 10 deferred reds. Green: the 20
  layout CLI tests, the 13 reset CLI tests (including a new
  `__pycache__`-littering pin found while turning the tree-identity
  assertions green - `lab-reset`'s module load of `lab-start` was
  dropping a cache directory beside the scripts), the 11
  template-plumbing tests, the 4 ignore-rule tests, and the 22
  pre-existing auth/refresh tests (retargeted onto managed labs under
  the fixture's `labs/` and the relocated template; assertions kept or
  strengthened). Deferred reds, both owned by Phase 3: the 9
  `scripts/migrate_002.py` contract tests (the stub still raises
  `NotImplementedError`) and
  `FreshCloneTests.test_fresh_clone_is_scaffolding_only` (a fresh clone
  of HEAD still carries the tracked lab trees, which Release untracks;
  `terraform-lab-scaffold/` is gone). No actual root lab tree -
  tracked or untracked - was touched.

  Adversarial review of the initial Build snapshot found three P1
  defects, independently reproduced in disposable
  `tests.util.make_repo` fixtures and remediated test-first
  (2026-11-06, follow-up `wip(002-phase2:` commit):

  * P1 traversal acceptance: `resolve_lab_target` checked only the
    final containment of the resolved path, so a target with literal
    `..` components that normalizes onto a managed lab was accepted -
    `labs/detour/../victim-lab` reset the victim lab's state from the
    repo cwd (existing `detour/` pivot), and `../labs/victim-lab`
    normalized inside `labs/` from a `labs/` cwd. `..` components are
    now refused BEFORE resolution; `.` components, absolute paths,
    and caller-relative paths stay valid. Pinned by reset and start
    traversal-normalizes-inside tests (dry-run and not) asserting
    byte-identical victim state/tfvars and zero gcloud, plus
    `labs/./<slug>` green pins.
  * P1 `--use-existing` gap: the missing-target refusal ran only in
    the positional branch, so a typed prompted name or a page-derived
    default with `--use-existing` scaffolded the missing lab
    (`labs/ghost-lab`) unattended. `require_existing_for_use` now
    runs after ANY target selection, before any side effect; the
    explicit new-name flow after a refused use path is preserved.
    Pinned by typed/prompted and page-default missing-target tests:
    nonzero exit, no replacement lab under `labs/` or the caller's
    directory, zero gcloud.
  * P1 generated-output symlink escape: refresh wrote in place, and
    every write follows symlinks - a managed lab's `README.md` symlink
    to an outside file got overwritten through the link, while
    DANGLING `backend.tf`/`terraform.tfvars`/`task<N>.tf` links
    (`.exists()` is False) had their referents created outside the
    lab. `refuse_symlinked_outputs` now fails safely before
    reset/fetch (positional) and before reset (prompted flows) for
    symlinked generated outputs; task links are user entries and are
    preserved as-is - scaffold treats `is_symlink()` as existing, so
    dangling task links and their referents are never touched, and
    the preview's creates/keeps note stays honest. Pinned by
    regular-target and dangling-output refusal tests (external file
    byte-identical / referent missing stays missing, lab state intact,
    no gcloud) and a dangling-task preservation test (refresh
    succeeds, link stays dangling, user work byte-identical). No
    transactional-refresh redesign; no recursive-deletion changes.

  P2 clarity fixes in the same pass: the preview's directory line now
  shows the resolved absolute target (the summary keeps the
  repo-relative name), a duplicated comment in `lab-reset` was removed,
  and `use_or_new` returns the resolved lab path instead of a
  misleading basename. Post-remediation suite: 90 tests - 80 green
  and the same 10 deferred Phase 3 reds as before the review (the 9
  migration contract errors and the fresh-clone layout failure); the
  10 new regression tests were demonstrated RED against that initial snapshot
  before the fixes.

### Phase 3: Release migration

Release evidence (2026-11-07, actual run of `scripts/migrate_002.py` on
this repository, `feat/002-directory-restructure`). A fresh preflight
matched the original external baseline manifest exactly: 15 tracked +
4 untracked labs, 0 collisions, 0 problems, name sets equal to the
manifest's, and per-lab counts equal (5008 manifest entries plus the
19 lab roots the helper records, 5027 extended attributes, 1,442,895,561
bytes). The full suite immediately before the run: 130 tests, 129 green,
the sole red the fresh-clone layout test (expected - HEAD still carried
the tracked labs). The run archived all 15 tracked labs, moved all 4
untracked labs, and staged exactly the 67 tracked lab-file deletions -
no other staged entry existed. Independent post-run comparison against
the original manifest (fresh code, not the helper's own verification
path): all 15 zip payload hashes, stored permission bits, sidecar entry
sets/types/modes/xattrs/symlink targets, and the 4 moved trees'
file hashes, directory/root modes, and xattrs matched with zero
discrepancies (3,593 files, 143 symlinks, 5,027 xattrs, exact bytes).
All 15 archives were restored into private disposable directories and
re-verified byte-identical (types, modes, link targets, xattrs) before
the copies were deleted. The disposable-clone smoke passed all 30
checks; the full suite passed 130/130 after the Release commit.

- [x] Re-inventory tracked, modified, untracked, and ignored lab contents.
  Produce a tracked-to-archive and untracked-to-labs mapping; check all
  collisions before changing anything. Pause lab commands during migration.
  (Fresh preflight matched the original manifest: 15 tracked, 4 untracked,
  0 collisions, 0 problems; lab commands paused during the run.)
- [x] Test and document any one-time migration helper; do not expose an
  archive subcommand or take on item 3's ongoing command workflow.
  (Helper landed with its 9 contract tests - deliberately red in
  Phase 2, green in Phase 3 - and is documented in the README's
  "one-time migration helper" section.)
- [x] Zip previously tracked labs into ignored local `archives/`. Preserve
  the recoverable local tree, reopen and verify every archive and any
  separate preservation copy before retiring sources. Keep failures recoverable.
  (15 zips + 15 sidecars, owner-only 0600, each verified at write time and
  re-verified before each source retirement; independent comparison of
  every payload hash and sidecar fact against the original manifest found
  zero discrepancies; all 15 archives restored and re-verified in
  disposable directories.)
- [x] Remove every inventoried lab-content path from the index with scoped
  `git rm --cached`, without deleting working files or touching unrelated
  staged changes. Stop on index conflicts; do not force or broadly re-add.
  Retire source directories only after verifying their local archives.
  (Staged set verified equal to exactly HEAD's 67 tracked lab files, all
  `D`; nothing else was staged before or after; sources retired only after
  their archive+sidecar re-verified immediately before each deletion.)
- [x] Move wholly untracked lab directories into `labs/`, including all
  hidden and ignored contents, without adding their files to the index.
  The user's 2026-11-06 instruction authorizes this destination policy;
  stop on unexpected classification changes rather than guessing.
  (4 labs moved by rename - design-secure-landing-zones-and-architecture,
  develop-serverless-apps-on-cloud-run, private-google-access-for-vsc,
  vpc-network-peering - each verified against its pre-move snapshot and
  the original manifest: 40 files, 21 directories, 4 roots, 65 xattrs,
  exact bytes; `git check-ignore` confirms all four trees ignored.)
- [x] Compare per-file hashes and relative paths before/after, including
  ignored state/tfvars and untracked task files. Check symlinks, permissions,
  module references, cached metadata, and generated paths. Neither archival
  nor moving may invoke reset, revoke credentials, destroy, or apply.
  (Every file hash, symlink target, permission bit, and xattr compared
  against the original manifest - zero mismatches; no gcloud, terraform,
  reset, or credential command ran during the migration. Portability
  read-only findings: all lab backends are lab-relative
  (`./terraform.tfstate`), module sources are registry-remote or internal
  to vendored `.terraform` caches, and no lab config holds an absolute or
  lab-escaping path; the vendored provider caches are platform-specific
  (`darwin_arm64`), so a restore onto another OS/arch needs a plain
  re-init - no backend migration is implied.)
- [x] Update root README links, `cd` examples, reset examples, script help,
  and active documentation. Label historical flat-layout examples in
  released specs as historical; retain migration source paths in this spec
  and the roadmap as history, not instructions for normal use.
  (README reframed as the clone/fork scaffolding tool with `labs/`
  examples; `lab-start`/`lab-reset` help carry only managed-lab examples;
  001 and 004 carry historical-layout notes; this spec and the roadmap
  keep source paths as history.)
- [x] Reframe the root README as a clone/fork-ready scaffolding tool.
  Remove completed-lab catalog links and describe local-only `labs/` and
  `archives/` with generic examples. Do not link to ignored archives that
  do not exist in a fresh clone. Document that Git history is unchanged.
  Verify no active docs promise old-root support or a public template
  selector. Retain safe old-target refusal tests.
  (No completed-lab links or archive links remain; "Git history is
  unchanged" stated; no `--template` selector, legacy flag, or old-root
  fallback in help, README, or code; old-root refusal tests stay green.)
- [x] Run the full suite and an offline smoke test in a disposable relocated
  checkout. Verify standard create/refresh, reset dry-run, internal template
  lookup, zip extraction, moved lab contents, and documented next steps
  without real credential changes.
  (Suite before the commit: 130 tests, 129 green, sole red the expected
  fresh-clone layout failure; disposable-clone smoke: 30/30 checks passed -
  standard create with next-step `cd labs/...`, refresh preserving user task
  work, reset dry-run, internal template lookup with no `--template`
  exposure, full migration in the clone (15 archived + fixture moved
  byte-identical with its xattrs), real-archive restore matching a
  pre-captured snapshot, and refusal of a pre-existing restore destination;
  stubbed/skipped auth and init throughout.)
- [x] Audit `git ls-files` against the original lab inventory: no lab
  content or archive payloads remain tracked; tool code, templates, docs,
  synthetic tests, and placeholders remain. Verify ignore behavior with
  `git check-ignore` for both local trees and archives.
  (`git ls-files` after the run: 27 paths - the two scripts, five template
  files, ten test files, README, AGENTS, .gitignore, the 002 helper, and
  the two .gitkeep placeholders, plus the .plans specs; all 30 archive
  files and all 4 moved lab trees come back ignored while
  `labs/.gitkeep`/`archives/.gitkeep` stay trackable; `git diff --check`
  clean.)
- [x] Mark Release and move item 2 to Done only after the actual lab trees
  are archived or moved as specified. Do not treat a passing fixture-only
  test as release evidence.
  (Release marked and the roadmap item moved to Done in the same commit
  that stages the 67 lab deletions; the evidence above is the actual
  repository, not fixtures.)

### Final independent adversarial review

After the phase WIP commits were squashed, `openai/gpt-6-astra#xhigh`
reviewed the committed implementation and exercised its own disposable
failure reproductions. Its P1 findings were remediated test-first in
`fix(002):` commits: generated directories redirecting template copies,
wildcard Git pathspecs removing unrelated index entries, sidecar-only names
that restoration rejected, and explicit nested aliases bypassing target
refusal. Follow-up review exposed deeper alias variants and case-equivalent
repository paths; the final boundary fix uses filesystem identity rather
than path-string equality.

Related P2 fixes cover interrupted-attempt cleanup, case and Unicode
normalization ambiguity, special permission bits, and the instructions
template's relative reset command. Automated preflight tests remain
synthetic and do not inventory the user's real working tree.

The final review approved code commit `dff8cc8`, with no remaining P0/P1
findings, blocking acceptance gaps, or optional P2/P3 comments. Astra ran
176 tests: 175 passed, with one expected filesystem-dependent skip. Actual
migrated data was not modified or re-migrated during these remediations.
See [the implementation summary](002-implementation-summary.md) for the
preservation audit and final commit structure.

## Edge cases

- A destination already exists: stop before moving any lab; never merge
  source and destination trees or choose a winner by timestamp.
- A move fails partway: leave both locations intact, report what moved,
  and resume against the inventory. Never delete an unverified remainder.
- An archive fails verification or a zip already exists: keep the source,
  report the failure, and do not overwrite or treat the archive as complete.
- A lab holds state for live resources: preserve it byte-for-byte. This
  restructure does not destroy resources or solve pre-existing stale state.
- A local clone contains nested Git metadata: preserve it locally and do
  not accidentally add it as a submodule. Any future vendoring policy is
  outside this item.
- A template file is missing: fail before resetting an existing lab or
  creating partial generated output. Validate the selected template only.
- Moving lab-relative modules as a whole should preserve their paths, but
  cached `.terraform` metadata may need reinitialization. Investigate before
  running init against live state; do not assume a backend migration is needed.

## Decisions confirmed and open questions

Confirmed by the user on 2026-11-06:

1. Build template plumbing, default to `standard`, and do not expose `--template`.
2. No legacy support is needed. This overrides the roadmap's general
   side-by-side Build rule for item 2 only.
3. Archive all tracked labs; move untracked labs into `labs/`.
4. Archives stay local-only and gitignored. Remove all lab content from
   tracking; keep this repository focused on scaffolding for clone/fork users.

No blocking product questions remain. Implementation must still inventory
local contents, check index conflicts, and verify recoverability before any
source retirement. Those are execution checks, not permission to discard data.

## Acceptance criteria

### Plan

- [x] This spec exists and records researched behavior, proposed decisions,
  implementation phases, safety boundaries, open questions, and acceptance criteria.
- [x] Template exposure, compatibility, migration, and archive/tracking policy resolved.

### Build

- [x] The target layout exists in a fresh clone; standard template history
  follows the move and new standard output preserves existing behavior.
  (git mv preserved blob ids; ignore-rule and plumbing tests green. The
  fresh-clone tree itself stays red until Release untracks the lab
  content - pinned by `FreshCloneTests`.)
- [x] New labs scaffold into `labs/` from any invocation directory;
  collision checks, refresh, reset, preview, and next steps agree on the target.
  (layout and reset CLI suites green.)
- [x] Internal template plumbing defaults to `standard`; the Terraform
  instructions template exists but no selector or alternate CLI mode is exposed.
  (template-plumbing suite green; no `--template` in `--help`.)
- [x] No legacy compatibility is added; old-root and destructive targets
  fail without file or credential changes.
  (old-root, collection-root, traversal, file, and symlink refusals
  asserted byte-identical with no gcloud calls, dry-run or not.)
- [x] Hermetic tests cover these boundaries and all existing critical
  authentication/refresh guarantees remain green.
  (22 auth/refresh tests green on the isolated fixture.)
- [x] Working labs and archives are ignored except tracked placeholders;
  synthetic tests work without checked-in completed labs.
  (`git check-ignore` verified; ignore-rule tests green against the
  real `.gitignore`.)

### Release

- [x] All previously tracked labs have verified local-only zips under
  `archives/`; all wholly untracked labs are under `labs/`. Local-only work
  and ignored data are preserved and verified.
  (15 zips + sidecars verified against the original manifest - 3,553 file
  hashes, 143 symlink targets, 4,962 xattrs, all modes; 4 labs moved under
  `labs/` - 40 files, 21 directories, 4 roots, 65 xattrs; all 15 archives
  restored and re-verified in disposable directories.)
- [x] No actual lab content or archive payloads remain in Git's index;
  only the tool, templates, docs, synthetic tests, and placeholders ship.
  (`git ls-files`: 27 non-lab paths; staged set exactly the 67 tracked lab
  deletions.)
- [x] No active code/help/docs rely on the flat layout or old template
  location; no compatibility paths remain and historical examples
  cannot be mistaken for current instructions.
  (Help, README, and templates audited; 001/004 marked historical; old-root
  refusal tests retained green.)
- [x] Archives and working labs remain local and gitignored. Untracking
  does not delete local files; later source retirement happens only after
  archive verification. No history rewrite, reusable archive command, or
  automatic completion detection has been added.
  (Untrack was `--cached`-only; each of the 15 sources was retired only
  after its archive+sidecar re-verified; `git check-ignore` ignores all
  30 archive files and 4 lab trees; no history rewrite - the deletions are
  ordinary staged changes; item 3 still owns the archive command.)
- [x] Fresh-clone documentation presents a scaffolding tool with no links
  that require this user's local labs or archives.
  (README describes generic `labs/<slug>/` and `archives/<slug>.zip`; no
  completed-lab catalog or archive links; fresh-clone test green after
  the Release commit.)
- [x] Full tests and disposable-checkout smoke checks pass; the roadmap
  reflects actual Build/Release evidence rather than intentions.
  (130/130 green post-commit - the fresh-clone test went green against
  the released HEAD; disposable-clone smoke 30/30.)

## References

- `.plans/ROADMAP.md`: item 2 scope and its no-legacy exception; item 3 owns the archive command.
- `.plans/004-existing-lab-dir.md`: standard refresh and user-work preservation contract.
- `lab-start`: path constants, validators, scaffold, refresh helpers, and main flow.
- `lab-reset`: argument resolution and recursive cleanup boundary.
- `terraform-lab-scaffold/`: current source of the standard template move.
- `tests/util.py`, `tests/test_refresh_cli.py`, `tests/test_refresh_component.py`,
  `tests/test_auth_cli.py`, `tests/test_auth_component.py`: fixture and regression coverage.
- `scripts/migrate_002.py`: contract-only stub for the one-time migration
  helper; `tests/test_layout_cli.py`, `tests/test_reset_cli.py`,
  `tests/test_template_plumbing.py`, `tests/test_git_boundaries.py`,
  `tests/test_migration.py`: the Phase 1 red contracts.
- `.gitignore`, `README.md`: nested-layout exclusions and operational instructions.
