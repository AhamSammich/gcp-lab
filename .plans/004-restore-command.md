# Restore through `labkit`

Date: 2026-11-11
Status: Implemented
Author: AI-assisted

## Background

Plan 003 deliberately kept recovery in `scripts/restore_lab.py`. The owner
now wants recovery behind the same CLI as archive. The script already calls
the shared preservation core; this changes the interface, not the format.

## Goal

Replace the standalone script with `labkit restore ZIP DEST`.

## Acceptance Criteria

- **RESTORE-1: Command.** Provide top-level discovery and command-local help.
  Invalid arguments exit 2 before side effects. Help requires no Playwright or
  external tools. Restore works from outside a checkout whose path has spaces.
- **RESTORE-2: Safety.** Keep old and numbered version-1 recovery and the existing
  validation, fresh-destination, input-pair, and attempt-owned cleanup safeguards.
  Exit 0 on verified success, 1 on refusal, and 130 on Ctrl-C, retaining recovery
  and remnant reporting. Keep meaningful preservation regressions.
- **RESTORE-3: Migration.** Archive success and partial-failure guidance emits
  shell-quoted `labkit restore` commands runnable outside the checkout, even
  after deleting the original cwd. Update active docs and disposable-clone
  tests; remove the old script. Historical plans remain unchanged.

Exclusions: compatibility wrapper, archive-format changes, merge/overwrite,
automatic recovery, new cloud operations, and preservation-core redesign.

## Verification

The focused command, recovery, archive, docs, and clone tests failed before
implementation and passed afterward (63 tests). The full
`python3 -I -B -m unittest discover -s tests -v` suite ran 203 tests successfully
with two skips: distinct-case directories on this case-folding filesystem and
native Linux xattrs on macOS. Tested with Python 3.14.7 on macOS; no new claims
for other interpreters or platforms. `git diff --check` passed.

All recovery work used disposable fixtures with stub-only PATH and temporary
HOME. Checks exercised old and numbered pairs, corrupt metadata, refusal of an
existing destination, interruption cleanup and exit 130, and runnable archive
success/partial-failure guidance from outside a spaced checkout. The success
case also archived from a cwd that archive removed. Real labs and credentials
were not used.
