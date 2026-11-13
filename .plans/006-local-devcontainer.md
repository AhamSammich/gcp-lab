# Local devcontainer fixes

Date: 2026-11-12
Status: Implemented (local ARM64 smoke validated)
Author: AI-assisted

## Background

The initial container references an unavailable Google Cloud feature and invalid
optional-tool versions. Its global credential volume also mixes local checkouts.
Existing configuration tests pass without building or running anything.

## Goal

Provide a small, headless local development container with useful validation and
honest setup instructions.

## Acceptance Criteria

- AC1: The container builds and starts as a non-root user with Python 3.10+,
  Terraform, gcloud, kubectl, and the GKE auth plugin on PATH. Use a supported,
  explicit distro and document the tool update policy and validation limits.
- AC2: Fresh credential storage is writable without root, persists across a
  rebuild, and differs between checkouts. Both gcloud configuration and ADC stay
  in that volume by default. Never mount or modify host gcloud credentials.
  Document inherited/explicit override caveats and intentional credential cleanup
  that does not remove lab work.
- AC3: Document local Docker/editor prerequisites, host-browser gcloud login,
  the unsupported headed lab-page fetch and manual fallback, Codespaces costs and
  local-work loss risks, and Flox's unvalidated status.
- AC4: Ordinary tests run without Docker/cloud access. An opt-in integration test
  builds/starts the real configuration and checks AC1/AC2 using only synthetic
  configuration and ADC, with no cloud resources, login, or revocation. Clearly
  separate static safeguards from runtime evidence.

## Exclusions

No Flox repair, CLI/auth refactor, desktop/browser framework, real lab login,
cloud resource operations, or general supply-chain framework. Runtime blockers
must be reported, not converted into passing/skipped integration results.

## Initial validation (2026-11-12)

The original feature-name assertions could not detect unavailable packages or
unusable storage. They were replaced with static mount/environment/user safety
checks and a real opt-in lifecycle test; Flox and existing documentation checks
were retained unchanged. Before implementation, all three new static checks
failed and the runtime test failed resolving the original Google Cloud feature.

Google's repository metadata lists the CLI, kubectl, and GKE plugin for both amd64
and arm64. The selected `1-bookworm` image tag exists in MCR. Package availability
does not prove runtime behavior on both architectures.

Executed after implementation:

- Focused static suite: 6 tests passed.
- Full suite: 273 tests run, 3 skipped, no failures (existing 2 skips plus opt-in runtime).
- Opt-in lifecycle test: passed on linux/arm64, including a no-cache rebuild,
  actual gcloud configuration persistence, synthetic ADC persistence, writable
  storage, checkout separation, and credential-volume removal without lab loss.
  Observed Terraform 1.17.2, Google SDK 593.0.0, kubectl client
  1.36.6-dispatcher, and a successful GKE plugin version command.
- `git diff --check`: passed.

At that point, amd64 runtime, real student authentication, GKE connection, VS Code
UI attachment, Codespaces, and an end-to-end lab had not been tested. The headed
Playwright fetch was intentionally unsupported; manual input was the documented
path. Flox was outside this change; its subsequent work is recorded in
[007](007-flox-environment.md). Build images/caches remained; smoke-test containers
and credential volumes were removed without accessing host credentials.

## Implementation decisions

The Dockerfile uses the explicit Debian 12 Bookworm base and distro Python 3.11.
Google's signed apt repository supplies gcloud, kubectl, and the GKE auth plugin,
avoiding the unavailable feature from the original configuration. Terraform uses
the maintained Dev Containers feature with optional linters disabled. The
feature lockfile records feature artifacts and their dependency, not a fully
pinned base image or apt toolchain. Tool versions can change on a rebuild.

The build context is `.devcontainer`, not the checkout, so ignored lab work and
credentials elsewhere in the repository are not sent to the builder. The
credential directory is created with `vscode` ownership in the image so Docker
can populate a fresh named volume without recursive ownership changes on every
start. `${devcontainerId}` separates local checkouts while retaining credentials
through rebuilds. Neither this mount nor the smoke test establishes isolation
from every possible explicit credential override.

The environment remains headless. Adding Playwright alone cannot provide the
visible first-login window required by the current browser-fetch code. We did
not add VNC, a desktop, browser-session transfer, or HTTP-only instruction parsing.

## Runtime test procedure

Install the [Dev Container CLI](https://github.com/devcontainers/cli), start the
local Docker daemon, and run from the checkout root:

```sh
LABKIT_TEST_DEVCONTAINER=1 python3 -m unittest discover -s tests -p test_devcontainer_runtime.py -v
```

The test downloads/builds tools and uses two scratch checkouts. It checks
non-root execution, executable versions, writable configuration, synthetic ADC,
checkout separation, a no-cache rebuild, and credential-volume removal without
losing scratch lab work. It does not log in, revoke credentials, or create cloud
resources. Only scratch containers and credential volumes are removed; build
images/caches remain. Opt-in setup/build failures fail the test rather than skip.
The ordinary suite skips it and needs neither Docker nor cloud access.

## Owner testing and documentation follow-up (2026-11-13)

The owner reported successful VS Code devcontainer attachment and gcloud login.
That establishes more than the original synthetic test, but does not establish
ADC use by Terraform, GKE connectivity, or a completed lab. Authenticated page
fetching still failed in the headless container.

The owner also encountered a macOS `.venv` mounted into Linux. Its Playwright
launcher referenced a macOS interpreter, so it could not execute. Re-running
venv creation over that directory did not remove the mixed interpreter links.
No fix for portable/shared virtualenvs or the generic installation hint was made.

AC3's user instructions now live in [.devcontainer/README.md](../.devcontainer/README.md),
linked from the root README. This keeps setup, authentication, persistence,
cleanup, Codespaces warnings, and current limits close to the environment while
keeping build rationale and test evidence here. The documentation change is not
a new container build or an extension of supported browser functionality.
