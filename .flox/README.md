# Native Flox environment

Use Flox to run the lab tools in a native shell without Docker. This configuration enables only Apple Silicon macOS (`aarch64-darwin`). It includes Python 3.13, Python Playwright, Terraform, gcloud, and `kubectl`. Native browser sign-in and lab-page extraction have been demonstrated; completing an entire lab has not.

## Start the environment

Install [Flox](https://flox.dev/docs/install-flox/). Package resolution and upgrades may require a FloxHub login:

```sh
flox auth login
```

From the checkout root, use the credential-checking wrapper, not raw `flox activate`:

```sh
bash .flox/activate
```

Run lab commands in this shell and type `exit` when finished. For a single command without an interactive shell:

```sh
bash .flox/activate -- python3 --version
```

The wrapper rejects inherited `CLOUDSDK_*`, `GOOGLE_APPLICATION_CREDENTIALS`, and `KUBECONFIG` variables, including empty values. Exit other environments first; unset a reported variable only if you understand why it was set. Do not nest activations, source/eval the wrapper, or pass Flox options to it.

If your zsh startup reports a gcloud completion error, exit the environment and try a Bash session without changing your default login shell:

```sh
FLOX_SHELL=/bin/bash bash .flox/activate
```

## Fetch lab instructions

Inside the activated environment, ensure the matching Chromium browser is available:

```sh
python3 -m playwright install chromium
./labkit start my-new-lab --url YOUR_LAB_URL
```

Playwright locates its browsers directly; `which chromium` can return nothing even when fetching works. The install command may also finish without output. Do not substitute the Node.js `playwright` package or a headless-shell-only installation: LabKit imports Python Playwright and needs a visible browser for website login.

For sign-in-walled pages, Chromium opens for your Google Skills website account. Sign in there; LabKit continues after you return to the lab page. It stores the session and tries headless fetching on later runs, reopening a visible window if the session expires. This is interactive authentication, not an unattended login mechanism.

Successful fetching can prefill instructions, task headings, and some live values from a running lab. It is best-effort: verify the result and enter any missing project, region, or zone from the lab. Anonymous HTTP currently reads only the page title and description; full HTTP-only instruction extraction has not been implemented. You can always omit `--url`, press Enter at the URL prompt, and enter values manually.

Avoid a `.venv` shared with the container or another Python version. The Flox packages supply Playwright directly; LabKit's generic `.venv` installation hint is not the setup procedure for this environment.

To refresh an existing lab, supply its name explicitly:

```sh
./labkit start your-existing-lab --use-existing --url YOUR_LAB_URL
```

Refresh deletes local state, revokes credentials, and rewrites generated files. Read [restart a lab](../README.md#restart-a-lab) before using it; it is not a way to reconnect to resources you still need to manage.

## Authenticate cloud tools

Website sign-in does not authenticate gcloud. LabKit separately offers `gcloud auth login --update-adc` for cloud tooling. Use the lab's temporary student account for that step, not your training or personal account. You can also run that command manually inside the activated environment and set the assigned project, region, and zone.

If gcloud warns about `GOOGLE_APPLICATION_CREDENTIALS`, its override and ADC output paths should both point to this checkout's `.flox/credentials/gcloud/application_default_credentials.json`. Stop if they differ. Follow the [LabKit quickstart](../README.md#quickstart) for the remaining lab workflow.

## Credentials and cleanup

The wrapper selects ignored checkout-local storage:

| Data | Location |
| --- | --- |
| gcloud configuration and ADC | `.flox/credentials/gcloud/` |
| Kubernetes configuration | `.flox/credentials/kube/config` |

These directories are owner-only. Symlinked or foreign-owned credential storage is refused. Credentials persist after exit and tool updates; fresh checkouts use separate stores. Do not copy the credential directory between checkouts.

Preview cloud-credential cleanup inside the activated environment:

```sh
./labkit reset --credentials-only --dry-run
# Inspect the selected account before proceeding.
./labkit reset --credentials-only
```

This neither destroys cloud resources nor clears the separate website session. See [cleanup and credentials](../README.md#cleanup-and-credentials) for the full workflow.

Flox is not a security sandbox. HOME, shell startup files, and LabKit's `~/.cache/labkit/` remain shared with the host. That cache includes the website session, rendered HTML/screenshots, and logs; a running lab's snapshots may contain temporary credentials. Do not commit, share, or upload those artifacts. Raw `flox activate`, explicit credential flags, provider settings, and variables changed after entry bypass the wrapper's defaults. Do not use personal cloud credentials in the lab shell.

## Limits and updates

Linux, Intel macOS, and Windows are not enabled by this manifest. The locked SDK does not include the GKE auth plugin, so this environment does not provide GKE access out of the box. Check kubectl's supported version skew before adding cluster access.

The manifest and lockfile select the Python toolchain together. To update packages, exit the lab shell, run `flox upgrade` from the checkout root with an authorized FloxHub session, and review the manifest and lockfile changes. Recheck Python Playwright and its matching browser after upgrades. Package downloads must remain available; the browser install is a separate setup step, not a guarantee made by the Flox lockfile.

The [implementation and validation record](../.plans/007-flox-environment.md) distinguishes the earlier automated tooling checks from the current browser workflow and documents outstanding test gaps.
