# Flox environment

Date: 2026-11-12
Status: Initial native tooling smoke-tested; current browser workflow manually demonstrated; automated follow-up outstanding
Author: AI-assisted

## Background

The manifest lacks environment metadata and a lockfile. Package isolation alone
does not keep LabKit login/reset away from personal gcloud credentials. Flox
1.11.4 is available locally; activation hooks can be reused across shells.

## Goal

Provide a reproducible native tool environment with an explicit, protected entry
point, `.flox/activate`. Check inherited credential overrides on every invocation,
before invoking Flox. Keep the flow understandable: validate, select local paths,
activate. Raw `flox activate` is not the protected entry point.

## Original Acceptance Criteria

- F1: Commit complete local environment metadata and resolved tooling lockfile.
  Use Python 3.11 (compatible with the project's 3.10+ requirement), Terraform,
  gcloud and kubectl. Claim platforms and GKE plugin support only from evidence.
- F2: The supported entry point refuses inherited `CLOUDSDK_*`,
  `GOOGLE_APPLICATION_CREDENTIALS`, and `KUBECONFIG` overrides before activation.
  Store gcloud/ADC/kubeconfig beneath ignored, owner-only checkout storage.
  Refuse symlinked or foreign-owned credential storage. Separate checkouts do
  not share credentials; synthetic commands and exit leave host config unchanged.
- F3: Document prerequisites, entry/exit, student authentication, persistence,
  updates and limits without claiming a sandbox or fixed lab-page fetching.
- F4: Ordinary tests need neither Flox nor cloud. Opt-in runtime tests activate
  scratch checkouts, check actual tool versions and synthetic credential
  persistence/separation. Setup/build failures fail rather than skip.

Exclusions: devcontainers, CLI/auth/fetch implementation, existing virtualenvs,
browser profiles, real credentials, labs/archives, cloud calls, real GKE access,
and authentication. No promise to isolate arbitrary provider flags or commands
that deliberately replace the selected environment variables.

## Initial implementation and automated validation (2026-11-12)

The original missing-lockfile test and runtime test were red before
implementation. The owner subsequently authorized use of their existing
FloxHub login, which unblocked resolution without another login or reading
tokens. The first resolution with `python3` constrained to 3.11 selected a July
2024 package group. Catalog evidence showed `python311` provides the current
3.11 patch series. Switching to that attribute and running `flox upgrade`
resolved all four tools against revision
`177d0540942fb205af6993b1d5fa0b57799e09f8` (2026-11-09).

The reviewed, CLI-generated lockfile contains only `aarch64-darwin` packages:
Python 3.11.18, Terraform 1.17.1, Google Cloud SDK 592.0.0, and kubectl 1.37.2.
No other platform is claimed. The locked SDK lacks `bin/gke-gcloud-auth-plugin`;
GKE connectivity is excluded as shipped. Terraform's BUSL-1.1 license requires
the manifest's nonfree allowance.

Runtime checks use fake HOME/XDG directories, prove executable provenance from
Flox, and preserve all original safety assertions. Native testing exposed two
fixture failures before correction: macOS's Unix socket path limit required a
shorter scratch/cache path, and gcloud inspected the non-JSON synthetic ADC
marker during project setting. The fixture now verifies no account or ADC
exists, sets the local project without remote access validation, then writes
the marker. No real account or synthetic token was used. An earlier attempt
to build the stale Terraform failed fetching Go dependencies over IPv6; the
current locked toolchain built and passed.

Executed checks:

- `python3 -m unittest discover -s tests -p test_flox_environment.py -v`:
  four passing preflight tests.
- `python3 -m unittest discover -s tests -p test_environment_configs.py -v`:
  six passing checks, including the unchanged required-lockfile assertion.
- `LABKIT_TEST_FLOX=1 python3 -m unittest discover -s tests -p test_flox_runtime.py -v`:
  one passing integration test, four actual activations across two checkouts.
  Tool versions, scoped paths, private directory modes, synthetic persistence,
  checkout separation, and unchanged fake host ADC/config/kubeconfig passed.
- `python3 -m unittest discover -s tests`: 278 tests run, four skips, no failures.
  Container checks were not changed or opted into.

At that point, real Google authentication, revocation, GKE, authenticated fetching,
and an end-to-end lab were untested. Raw `flox activate` bypassed the supported
entry point, and the environment was not a security sandbox. The agent changed no
actual credentials, virtualenvs, browser profiles, labs, archives, or container
configuration. Scratch environments were removed; downloaded Nix packages and
Flox cache/registry bookkeeping remained. Nothing was staged or committed.

## Implementation decisions

The wrapper checks credential overrides on every invocation, rather than using a
Flox activation hook that can be reused across concurrent shells. It validates
checkout-local storage, sets gcloud/ADC/kubeconfig paths, and invokes Flox. The
flow deliberately does not change HOME or claim to isolate arbitrary explicit
credentials, browser profiles, shell startup files, or the shared LabKit cache.
No automatic credential deletion runs on exit. The nonfree-package allowance
permits the Terraform license; it is not evidence of GKE plugin availability.

The original test-only Python 3.11 choice was not a LabKit language constraint;
the project requires Python 3.10+. The exact lockfile and tested platform matter
more than assuming any package named `playwright` supplies the Python API.

## Owner testing and current configuration (2026-11-13)

The owner updated the manifest and generated lockfile to `python313` and
`python313Packages.playwright`. Their reported environment contains Python
3.13.18, Python Playwright 1.64.0, Terraform 1.17.1, gcloud 592.0.0, and kubectl
1.37.2. The unqualified `playwright` package had resolved to Node's
`playwright-core` with no executable directory; it was not the library LabKit
imports. The older Python 3.11 catalog entry offered Playwright 1.47.0, so the
owner selected the newer matching Python/Playwright pair instead.

After `python3 -m playwright install chromium`, the owner ran LabKit in the native
environment. Chromium reopened visibly for an expired Skills website session,
sign-in completed, and extraction returned the PP007 lab title, four content
sections, eight task headings, and a running lab's project ID. A zone still had
to be supplied manually. Do not record the actual project or student credentials
here. This demonstrates browser sign-in and extraction, not successful Terraform
authentication, complete variable extraction, GKE access, or a completed lab.

The browser executable need not be on PATH. A separate headless-shell-only
installation cannot display the login window. Browser downloads/selection and
the persisted website profile are distinct from the Flox package lock and
checkout-local cloud credentials.

An interactive zsh run also reported a host gcloud completion error. The exact
error was reproduced by sourcing the host completion script with `KSH_ARRAYS`
enabled, but the source of that option in the owner's session was not established.
The guide offers `FLOX_SHELL=/bin/bash` as a workaround, not a claimed fix to zsh
or the owner's shell configuration. Later reported activation did not show the
error.

The shared `.venv` was removed by the owner, not by the agent. The setup uses the
Flox Python packages directly. We have not implemented HTTP-only full instruction
extraction: the existing urllib branch reads title/meta only. An anonymous
request to the partner lab redirects to website sign-in; gcloud authentication
does not establish that session. The owner supplied page-source excerpts showing
static instructions in authenticated HTML, which motivates a separate future
fetching change but is not functionality delivered by this environment work.

## Current automated coverage gap

The earlier passing runtime result applies to Python 3.11 without Playwright.
`tests/test_flox_runtime.py` still asserts `sys.version_info[:2] == (3, 11)`.
It is incompatible with the current Python 3.13 manifest and does not launch
Chromium. Do not cite its historical pass or an ordinary suite that skips it as
automated validation of the current browser configuration.

The existing opt-in command is:

```sh
LABKIT_TEST_FLOX=1 python3 -m unittest discover -s tests -p test_flox_runtime.py -v
```

Updating that test to the accepted current Python toolchain and adding meaningful
browser launch/extraction coverage are outstanding work, not part of this
documentation-only change. Existing credential-boundary regressions must be
preserved. Real student gcloud authentication under Flox, revocation, GKE,
end-to-end lab completion, and other platforms remain unvalidated.

F3's user instructions now live in [.flox/README.md](../.flox/README.md), linked
from the root README. They describe the current package setup, native browser
workflow, separate website/cloud accounts, cleanup, and support limits without
presenting initial implementation results as current test evidence.

## Documentation-only verification (2026-11-13)

The AC3/F3 documentation tests first failed because the root did not link to the
environment guides and the guides did not exist. After the split, the ordinary
suite ran 279 tests with four skips and no failures. Fourteen local Markdown
links, including heading anchors, and code-fence balance were checked across the
root README, both environment guides, and both environment plans. Whitespace
checks passed. No container/Flox runtime tests, browser sessions, real logins, or
cloud operations were run for this documentation update. The stale opt-in Flox
runtime assertion described above is unchanged.
