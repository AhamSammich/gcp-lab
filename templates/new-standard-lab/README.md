# __LAB_TITLE__

[Go to lab at skills.google](__LAB_URL__)

__LAB_SECTIONS__

## Cleanup and preservation

Destroy resources first if needed, while state and credentials are available.
For ordinary local cleanup from this lab directory, `../../labkit reset`
removes state and tfvars and revokes credentials; it does not destroy resources.
To keep the full lab instead, return to the repository root, preview with
`./labkit archive <lab-slug> --dry-run`, run `./labkit archive <lab-slug>`,
then `./labkit reset --credentials-only`. The ignored archive pair includes
hidden and sensitive files. See the tool README for manual recovery into a
fresh destination and single-writer limits.

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
