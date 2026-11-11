# __LAB_TITLE__

[Go to lab at skills.google](__LAB_URL__)

__LAB_SECTIONS__

## About this lab

This lab supplies its own Terraform repository instead of the standard
skeleton. The lab page names the upstream repository to use and the
working subdirectory to select; nothing here invents those for you.

## Getting the lab's Terraform code

1. Start the lab and note the student account and other connection
   details on the lab page.
2. Clone the repository the lab page names into this lab directory -
   the upstream URL and branch are whatever the lab page says, never
   a value copied from another lab.
3. Select the subdirectory the lab instructs you to work in; the
   repository usually carries several lab variants.
4. Follow that repository's own instructions for initialization:
   its `terraform init` command, the variables it expects you to
   provide, and the backend configuration it asks for.

## Authenticating

Use the student account from the lab page. `gcloud auth login
--update-adc` covers both gcloud itself and the application-default
credentials Terraform authenticates with, in one browser flow.
For ordinary local cleanup, run `./labkit reset <lab-slug>` from
the tool repository root (or `../../labkit reset` from inside this
lab directory). This removes local state and tfvars and revokes credentials;
it does not destroy cloud resources.

To finish and keep the full lab, destroy resources first if needed while
state and credentials are available. From the tool repository root, preview
with `./labkit archive <lab-slug> --dry-run`, run
`./labkit archive <lab-slug>`, then `./labkit reset --credentials-only`.
The ignored archive pair includes hidden and sensitive files. See the tool
README for manual recovery into a fresh destination and single-writer limits.

## Installing Terraform in Cloud Shell

Copy/paste into Cloud Shell:

```
cat <<'EOF' > ~/.customize_environment
# Set up HashiCorp repository and install Terraform
wget -O - https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(grep -oP '(?<=UBUNTU_CODENAME=).*' /etc/os-release || lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update && sudo apt install -y terraform
EOF
bash ~/.customize_environment
```
