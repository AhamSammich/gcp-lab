# Local Dev Container

Use this setup for a Linux toolchain inside VS Code without installing cloud CLIs on your host. It includes Python 3.11, Terraform, gcloud, `kubectl`, and the GKE auth plugin. The container is headless: it does not provide a desktop or support LabKit's interactive browser fetch.

## Start the environment

Install Git, VS Code with its Dev Containers extension, and Docker Desktop or a compatible local Docker Engine. Start Docker, clone the repository locally, open the checkout in VS Code, and select **Dev Containers: Reopen in Container**. The initial build needs internet access. Opening the container does not create cloud resources.

Run lab commands in the container terminal from the checkout root. See the [LabKit quickstart](../README.md#quickstart) for scaffolding and the lab workflow.

## Authenticate and start a lab

Open the lab instructions in your host browser. Start without a URL and skip LabKit's default browser-auth step:

```sh
./labkit start my-new-lab --skip-auth
```

Press Enter at the URL prompt and enter the title, task count, and required lab values manually. Then authenticate in the container terminal, substituting the project and location assigned by the running lab:

```sh
gcloud auth login --no-launch-browser --update-adc
gcloud config set project YOUR_LAB_PROJECT_ID
gcloud config set compute/region YOUR_LAB_REGION
gcloud config set compute/zone YOUR_LAB_ZONE
```

Follow gcloud's URL and authorization-code prompts in a trusted host browser. Use the lab's student account, not your personal account. Do not share login URLs or codes. Google's [remote login documentation](https://cloud.google.com/sdk/gcloud/reference/auth/login#--launch-browser) also describes `--no-browser`, which requires a recent gcloud installation on the trusted host for the remote-bootstrap step.

The container sets `GOOGLE_APPLICATION_CREDENTIALS` to `/home/vscode/.config/gcloud/application_default_credentials.json`. If gcloud warns about this override, verify that both its override and ADC output paths match that location. Stop if either points elsewhere.

Website login and gcloud login are separate. Installing Playwright or mounting a host `.venv` does not supply a display for interactive website sign-in. LabKit may still print a generic Playwright installation hint; use manual lab input in this environment rather than following that hint. Virtualenvs should not be shared between macOS and Linux.

## Credentials and cleanup

gcloud configuration and ADC live in the checkout-scoped Docker volume `labkit-gcloud-config-<devcontainerId>`. It survives rebuilds and is separate from another checkout's volume; moving a checkout can change its ID. Host `~/.config/gcloud` is not mounted. Lab files stay in the locally mounted checkout.

This is not a security sandbox. Editor settings, dotfiles, credential overrides, and Terraform provider settings can change which credentials are used. Before login or cleanup, check the selected `CLOUDSDK_CONFIG`, `GOOGLE_APPLICATION_CREDENTIALS`, any other `CLOUDSDK_*` overrides, and:

```sh
gcloud info --format='value(config.paths.global_config_dir)'
```

Do not forward personal credentials into the container. Browser sessions, rendered lab pages, and logs under the container's `~/.cache/labkit/` may contain sensitive data; that cache is not covered by the gcloud volume's persistence guarantee.

To clear cloud credentials without removing lab files, run this inside the container:

```sh
./labkit reset --credentials-only --dry-run
# Inspect the selected account before proceeding.
./labkit reset --credentials-only
```

This does not destroy cloud resources or clear a Skills website session. See [cleanup and credentials](../README.md#cleanup-and-credentials) before removing state or finishing a lab.

To discard the whole local gcloud store, first close VS Code and identify the exact volume mounted at `/home/vscode/.config/gcloud` using `docker inspect <container-id>` on the host. Stop and remove only that container, then remove only that volume with `docker volume rm <exact-volume-name>`. Volume deletion removes local files; it does not revoke remote tokens. Never use volume pruning for this cleanup, and do not remove workspace storage. Nothing clears credentials automatically on rebuild.

## Limits and updates

The local ARM64 container has been smoke-tested, and VS Code attachment and gcloud login have been reported working. AMD64 execution, GKE connectivity, and a complete lab have not been validated. The included GKE auth plugin alone does not establish cluster connectivity. Check kubectl's supported client-version skew for your lab.

Rebuilds can pick up newer tools. The feature lockfile does not pin the entire image or apt-installed tools, so this is not a fully version-reproducible environment. Recheck tooling after updates; maintainers can use the runtime test described in the [implementation and validation record](../.plans/006-local-devcontainer.md).

GitHub Codespaces is remote, not this local Docker workflow, and has not been validated here. It can incur charges. A git push does not back up ignored `labs/` or `archives/`; make a separate secure backup before deleting a codespace.
