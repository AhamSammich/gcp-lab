# Item 002 implementation summary

Status: implemented and released on `feat/002-directory-restructure`.
Final independent Astra review: approved, with no remaining P0/P1 findings
or blocking acceptance gaps.

## What ships

The repository now ships a scaffolding tool instead of completed exercises.
`lab-start` creates `labs/<slug>/` from any invocation directory. Both helpers
use the same target resolver, reject old-root targets, traversal, collection
roots, files, and symlink escapes, and retain the resolved path throughout
refresh and reset. Previews show that path; printed `cd` commands are quoted.

The four standard template files moved unchanged to
`templates/new-standard-lab/`. Internal template plumbing defaults to
`standard`; `templates/new-terraform-lab/README.md` contains upstream-repository
instructions only. No public selector, automatic template detection, legacy
fallback, or alternate CLI workflow was added.

Only `labs/.gitkeep` and `archives/.gitkeep` remain tracked in those
collections. The README describes cloning/forking the tool, local-only work,
refresh/reset risks, and recovery from the one-time migration. Item 3 still
owns the reusable archive command.

## Actual migration and preservation

On 2026-11-07, 15 previously tracked lab trees became owner-only local zip
archives with verified metadata sidecars under `archives/`. Four wholly
untracked trees moved intact under `labs/`:

- `design-secure-landing-zones-and-architecture`
- `develop-serverless-apps-on-cloud-run`
- `private-google-access-for-vsc`
- `vpc-network-peering`

The migration removed exactly 67 inventoried lab paths from Git's index
with scoped index-only operations. Sources were retired separately, only
after verifying their archive and sidecar. No credentials were changed and
no real Terraform initialization, apply, destroy, or lab reset ran.

The parent independently compared every archive and moved tree with a
pre-migration manifest: 3,593 file hashes, 1,272 directories, 143 symlinks,
5,027 extended attributes including lab roots, and 1,442,895,561 regular-file
bytes matched. All 15 archives were also restored and compared in disposable
directories. A separate verified private recovery copy remains outside Git.

Archives and sidecars are `0600`, local, and ignored. Both are needed for
full restoration; do not commit or share them. Git's existing history still
contains the previously tracked exercises. No remote purge or historical
rewrite was performed.

## Tests and adversarial review

All three phases were implemented sequentially by
`ollama/glm-5.3:cloud#high`, with parent adversarial review and test-first
remediation before proceeding. After a second GLM usage limit, the user
authorized a Sol subagent for the final filesystem-identity fix. The reviews
corrected unsafe traversal,
missing prompted refresh targets, generated-file symlink writes, mistaken
Git staging assertions, archive collision handling, symlink metadata loss,
late discovery of unsupported local entries, and incomplete sidecar
verification before source retirement. Final Astra review also drove fixes
for generated-directory redirection, Git wildcard pathspecs, unrestorable
metadata names, nested aliases, interruption cleanup, Unicode-equivalent
names, special permission bits, and a template's relative reset command.
Regression tests cover these paths.

`openai/gpt-6-astra#xhigh` independently reviewed the committed code and
reproduced the reported failures in disposable fixtures. After remediation
and repeated re-review, it approved code commit `dff8cc8` with no outstanding
P0/P1 findings or optional P2/P3 comments. No real labs, archives, sidecars,
credentials, or cloud operations were touched during that final review.

Verification after release:

- `python3 -m unittest discover -s tests`: 176 tests run; 175 passed and one
  filesystem-dependent case skipped. Astra independently confirmed this.
- Relocated disposable-checkout smoke: 30 checks passed at Release. After
  final remediation, a separate parent smoke passed 17 checks covering
  creation, refresh, dry-run reset, quoted next steps, template plumbing,
  synthetic migration/restoration, and Git boundaries. Both used temporary
  HOME, stubbed gcloud, and skipped Terraform initialization.
- Fresh-clone, tracked-file, ignore-rule, and whitespace audits passed.
- All original authentication and task/module-preservation guarantees remain
  covered; tests do not require completed labs or local archives.

Provider caches are platform-specific and may need ordinary reinitialization
after restoration on a different OS or architecture. Inspected backend and
module references did not require a backend migration.

The spec and roadmap record actual Release evidence, and item 2 is in Done.

## Commits

- `3693ca7`: `refactor(002): relocate unchanged standard templates`
- `c3a8734`: `feat(002): isolate scaffolding templates and local lab work`
- Subsequent `fix(002):` commits contain final-review remediations, ending
  with the reviewed filesystem-identity fix `dff8cc8`.

All phase WIP commits were squashed. The pure template move remains a
separate commit because a single combined diff makes Git assign three
identical template files to deleted lab sources instead of their original
template paths. This preserves `git log --follow` history for all four
templates. Existing main-branch history was not rewritten. Nothing was
pushed.
