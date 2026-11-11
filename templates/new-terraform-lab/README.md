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
When the lab is finished, revoke both by running `./lab-reset`
from the repository root, naming this lab (or, from inside this
lab directory, `../../lab-reset` with no argument).

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
