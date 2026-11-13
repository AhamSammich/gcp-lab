FROM mcr.microsoft.com/devcontainers/base:1-bookworm

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Google's signed apt repository publishes both amd64 and arm64 packages.
# Keep the build context in .devcontainer so labs/credentials never enter it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg python3 \
    && curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
       | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && echo 'deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main' \
       > /etc/apt/sources.list.d/google-cloud-sdk.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       google-cloud-cli google-cloud-cli-gke-gcloud-auth-plugin kubectl \
    && rm -rf /var/lib/apt/lists/*

# Docker copies this empty, user-owned directory into a fresh named volume.
# Do not recursively chown an existing credential volume on every start.
RUN install -d -m 0700 -o vscode -g vscode /home/vscode/.config/gcloud

USER vscode
