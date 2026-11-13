# One CLI: `labkit`

Date: 2026-11-08
Status: Released (2026-11-11); committed fresh-clone verification passed
Author: AI-assisted

## Background

`lab-start` scaffolds or refreshes a lab; `lab-reset` clears local state,
tfvars, and credentials. They currently depend on each other's executables.
The directory migration is complete, but its script still contains the
archive/restore code needed to recover existing labs.

The previous plan repeated requirements across contracts, architecture, phases,
and acceptance lists. This replaces that plan with one acceptance list. The
completed plans remain historical records, not extra implementation backlogs.

## Goal

Put start and reset behind one repository-local Python CLI and add archive for
one finished lab. Keep the code readable through ordinary imports and direct
calls, using the existing stdlib/dependency model. Keep the old launchers during
Build; choose the final name and remove them at Release.

The finishing workflow is archive, then `reset --credentials-only`. Destroy
cloud resources first if needed, while state and credentials still exist.
Archive does not destroy resources or sign the user out.

No installer, CLI framework, plugin system, migration compatibility API, bulk
archive, retention policy, automatic resume/merge, or public restore subcommand.
Concurrent writers, crash-atomic transactions, and native Windows are outside
scope. Module layout is an implementation choice, not a test contract.

## Acceptance Criteria

- [x] **CLI-1: Entry points.** `./labkit` provides `start`, `reset`, and
  `archive`, with command-local help. Help exits 0; invalid arguments exit 2
  before operations or logging. Help does not load Playwright or run external
  commands. During Build, old start/reset launchers used the same implementation;
  CLI-11 retires them in favor of the final name.
- [x] **CLI-2: Existing workflows.** Preserve existing start/reset options,
  prompts, generated files, cache/log paths, single-login authentication, and
  best-effort init/auth results. Refresh resets before scaffolding, revalidates
  its target immediately before cleanup, stops on local reset failure, and
  preserves task files and other non-generated work. It no longer needs the
  legacy executables internally. Keep managed-target and generated-path guards.
- [x] **CLI-3: Credential-only cleanup.** `reset --credentials-only` works
  without a lab and rejects a positional directory before side effects. It
  touches no lab files. Unset the current gcloud project/region/zone, revoke ADC,
  and revoke the active account confirmed by a successful lookup, with subprocess
  stdin closed. Continue independent steps after failures. Exit 1 for missing
  gcloud or any failed/unconfirmed step; ordinary reset stays best-effort.
  Dry-run only queries availability/account and may write the existing reset log.
- [x] **CLI-4: Archive boundary.** `archive [directory]` selects exactly one
  existing managed lab, defaulting to the current lab root as reset does. Empty
  and notes-only labs are valid. Refuse unsafe/symlinked targets or collections,
  source/output paths tracked in the tool checkout's HEAD or index, intersecting
  gitlinks, and non-ignored outputs before writes. Anchor read-only Git checks to
  the tool checkout despite cwd/environment overrides. Independent repositories
  inside a lab are allowed. Never change the tool index, siblings, outside files,
  credentials, or cloud resources; never invoke gcloud, Terraform, or reset.
- [x] **CLI-5: Numbered pairs.** Write owner-only (`0600`), gitignored
  `archives/<slug>--001.zip` / `<slug>--001.meta.json` pairs. Select one above
  the highest occupied generation, counting either artifact, including dangling
  links; pad to at least three digits. Leave unnumbered archives and all older
  artifacts untouched. Create exclusively; a collision fails without overwrite.
- [x] **CLI-6: Preservation.** Preserve the version-1 format and full lab tree,
  including ignored/hidden files, nested repositories, empty directories, file
  bytes, entry types, full modes, safe relative symlink targets, and xattrs,
  including root metadata. Do not follow links outside the tree. Refuse unsafe,
  ambiguous, or unsupported entries/metadata rather than silently losing data.
  Stream file payloads and hashes with bounded reads and ZIP64 support. Ownership,
  timestamps, and hard-link identity are not preserved.
- [x] **CLI-7: Verify before removal.** Reopen and verify both artifacts against
  the complete original inventory, then recheck source and pair before deleting
  the source. A failed write, verification, or source-change check leaves the
  source intact and cleans only artifacts still owned by this attempt. Once
  destructive cleanup starts, every failure or interrupt keeps the verified
  pair and reports the remaining source. Cleanup must not chmod outside
  hard-linked files or follow symlinks. Never discard the only recoverable copy.
- [x] **CLI-8: Preview and results.** Archive `--dry-run` performs read-only
  preflight/inventory, names source and destinations, explains source removal,
  and creates no logs, artifacts, or reservations. Archive exits 0 only after
  verified archival and source removal, 1 on failure, and 130 on Ctrl-C. Report
  exact recovery/remnant paths without masking the original error. Success and
  partial-failure guidance give shell-quoted restore commands usable from outside
  the checkout or a deleted lab cwd, including a command to return to the repo.
- [x] **CLI-9: Recovery and retirement.** Provide
  `python3 scripts/restore_lab.py ZIP DEST` for old and new version-1 pairs.
  Refuse existing destinations and validate metadata before extraction writes.
  On failure/interruption, clean only the attempt-owned partial destination and
  report any remnants; leave the input pair untouched. Verify round-trip facts
  independently of the production verifier. Remove `scripts/migrate_002.py`,
  migration orchestration, index mutation, and migration-only tests after
  extracting needed archive/restore code. Keep meaningful preservation regressions.
- [x] **CLI-10: Focused verification.** New/changed tests identify the criterion
  they verify and assert observable outcomes, including its meaningful failure
  modes. Use disposable checkouts, HOME, labs, and stub cloud tools; never use
  installed credentials or real lab data. Keep existing meaningful regressions
  during extraction. Run the relevant tests per change, then the full unittest
  suite, direct-launcher disposable-checkout smoke, and `git diff --check` before
  acceptance. Target Python 3.10+ on macOS/Linux; record actual versions,
  filesystem limitations, and skips, and claim portability only where tested.
- [x] **CLI-11: Release.** Settle the final command name, remove legacy
  launchers, and update active help, README, templates, and roadmap together.
  Document archive sensitivity, archive-before-cleanup, fresh-destination/manual
  recovery, and single-writer limits. Verify the committed executable in a fresh
  clone without real cloud credentials. Record owner workflow validation before
  marking Release complete; do not archive their labs to manufacture evidence.

## Build evidence (2026-11-08)

The owner approved CLI-1 through CLI-10 in this session. Each behavior slice
went through failing tests, implementation, coordinator diff inspection, and
adversarial review. All blocking findings were fixed and re-reviewed.

- CLI-1–3: entry, authentication, refresh, reset, layout, and target-boundary
  tests cover shared launchers, direct refresh cleanup, and strict
  credential-only failures, including stderr that must not become an account.
- CLI-4–8: `tests/test_archive_cli.py` covers Git boundaries, numbered exclusive
  pairs, independent preservation checks, bounded reads/ZIP64, source/pair
  changes, ownership-aware cleanup, interrupts, and usable recovery commands.
- CLI-9: `tests/test_restore_lab.py`, `tests/test_preservation.py`, and the frozen
  synthetic `tests/old_v1_fixture.py` check old/new recovery independently.
  Migration-only orchestration tests were retired; preservation regressions
  were retained or ported. README recovery commands no longer name the removed
  migration script.
- CLI-10: final `python3 -I -B -m unittest discover -s tests -v` in a disposable
  Git snapshot passed: **203 tests, one skip**, in 35.488 seconds. Final direct
  shebang smoke passed for all three launchers, start, refresh, reset, archive
  preview/write, the emitted restore command, and credential-only cleanup.
  Smoke used a spaced checkout, an outside cwd, and a lab cwd deleted by
  archive. Fixture index bytes and restored user work remained intact.
  `git diff --check` and whitespace checks for added files passed.

Final acceptance ran with disposable HOME/data and a restricted stub PATH.
Python 3.14.7, macOS 26.6.2 (Darwin 25.6.0), arm64, Git 2.50.1 were exercised.
The filesystem folds case and Unicode-equivalent names; the distinct-case
directory test skipped. The Python 3.10 cleanup branch was simulated, not run
on that interpreter. Linux and other Python versions remain untested.

Execution incident: an earlier implementer smoke used installed gcloud with a
temporary HOME but without a restricted PATH. The implementer reported no real
credential/config changes; that claim was not independently confirmed. The
owner was notified and authorized continuation with strictly stubbed checks.

CLI-11 is not accepted: actual-repository committed fresh-clone verification
is pending. No real lab archival,
credential cleanup, deployment, or release is authorized by this evidence.

## Owner validation (2026-11-11)

The owner reported that start, reset, and archive commands and the restore
script work. Refresh and credential-only cleanup were not separately reported
as validated. The exact invocations were not specified. Release remains
pending the remaining CLI-11 requirements.

## Naming decision (2026-11-11)

The owner selected `labkit` as the final command name and approved the rename,
legacy removal, and related help, README, and template updates. `labkit` is now
the sole executable; `cloud-lab`, `lab-start`, and `lab-reset` have been removed.

## Release implementation evidence (2026-11-11)

The CLI-11 rename, cleanup, and documentation slice passed test-first
implementation, coordinator diff inspection, and adversarial review with no
blocking findings. The internal `labkit_cli` package and historical
`~/.cache/lab-start/`, `lab-start.log`, and `lab-reset.log` paths are unchanged.
Workflow regressions now exercise `labkit` subcommands; no meaningful behavior
checks were removed with the legacy launchers.

Final `python3 -I -B -m unittest discover -s tests -v` in a disposable candidate
clone passed: **206 tests, one filesystem skip**, in 43.296 seconds. Direct
shebang smoke in that clone passed help, start/auth/init, refresh, ordinary
reset, archive preview/write, emitted standalone restore, and credential-only
cleanup. Checks covered outside and spaced paths, a deleted lab cwd, preserved
user work, unchanged index/input-pair bytes, and the historical log paths.
`git diff --check`, added-file whitespace checks, and Python 3.10 grammar checks
passed. Actual runtime remains Python 3.14.7 on macOS 26.6.2 (Darwin 25.6.0),
arm64, Git 2.50.1. Linux and Python 3.10 runtime remain untested; the
distinct-case-directory regression skipped on this case-folding filesystem.

Only disposable fixture repositories were committed and cloned for these
checks, with isolated HOME and restricted stub PATH. The actual checkout was
not committed. Candidate-clone success does not satisfy CLI-11's committed
actual-repository fresh-clone gate, so Release remains unchecked and item 003
stays active. Owner workflow reports are recorded above; no new real-data or
credential operations were run for the rename.

## Test-suite review and commit (2026-11-11)

The owner approved pruning repeated workflow smoke tests, incidental prose
checks, and internal template-contract tests. Delegated pruning removed 22
tests, moving unique assertions into surviving checks. Archive/restore safety
regressions and essential workflow guidance checks remain intact.

Verification confirmed Git-fixture isolation, valid-target reset dry-run and
recursive deletion checks, exact single-login assertions, and the Linux
no-follow xattr oracle. Reintroduced faults were caught by the regression tests.
The remaining auth-component environment leak was reproduced before adding
`clear=True`; the existing configuration test now supplies synthetic cloud
overrides without adding another test.

Final `python3 -I -B -m unittest discover -s tests -v` passed with **201 tests,
two skips**, in 44.998 seconds, using disposable HOME/data, restricted stub PATH,
and synthetic ambient cloud overrides. The skips cover distinct-case directory
identity on this case-folding filesystem and the native Linux xattr oracle on
macOS. Python 3.14.7 on macOS 26.6.2 arm64 was exercised; Linux ABI simulation
passed, but native Linux and Python 3.10 runtime remain untested.
`git diff --check` passed.

The implementation, documentation, and test changes are now committed. These
checks ran from the working checkout with disposable fixtures, not a fresh clone
of the actual committed repository. CLI-11 remains unchecked pending that gate;
the owner validation record above is unchanged.

## Committed fresh-clone Release verification (2026-11-11)

CLI-11 is complete. A fresh clone of actual repository commit
`49236b95e3c86e1f606633bbc178661b7d4fb31f`, created with `git clone --no-local`
in a path containing spaces, passed the committed-executable gate. The clone
started with only tracked placeholders under `labs/` and `archives/`, executable
mode `0755` on `labkit`, and no retired launchers or migration executable.

From that clone, `python3 -I -B -m unittest discover -s tests -v` passed with
**201 tests, two skips**, in 42.475 seconds. Tests used disposable HOME/data,
restricted PATH, and synthetic ambient cloud overrides. Python 3.14.7,
macOS 26.6.2 arm64, and Git 2.50.1 (Apple Git-155) were exercised. The skips
were the distinct-case-directory check on this case-folding filesystem and the
native Linux xattr oracle on macOS. Native Linux and Python 3.10 runtime remain
untested; this release does not claim verification on those platforms.

Direct-shebang smoke from outside the spaced checkout passed help and argument
refusal, start with stub Terraform init and single-login auth, refresh preserving
task/module bytes, ordinary reset preview/write, archive preview/write from a lab
cwd deleted by archive, the emitted standalone restore command, existing restore
destination refusal, and credential-only preview/write without a current lab.
Independent tree facts confirmed restored bytes, entry types, modes, and xattrs.
Checks also confirmed unchanged sibling work, input archive-pair bytes, the clone
Git index, and the original checkout/index, plus the historical cache/log paths.

Only Python, Git, and logging gcloud/Terraform stubs were reachable on PATH.
No installed cloud tool, real credential, real lab, or cloud resource was used.
The owner workflow reports recorded above remain unchanged: start, reset,
archive, and restore were owner-reported; refresh and credential-only cleanup
were verified here with stubs, not claimed as additional owner validation.
The previously pending fresh-clone gate is now satisfied.
