# Implement Priavate Google Access and Cloud NAT

_Last updated Sep 29, 2026_
[Go to lab at skills.google](https://skills.google/paths/78/course_templates/36/labs/630818)

## Overview

In this lab, you implement Private Google Access and Cloud NAT for a VM instance that doesn't have an external IP address. Then, you verify access to public IP addresses of Google APIs and services and other connections to the internet.

VM instances without external IP addresses are isolated from external networks. Using Cloud NAT, these instances can access the internet for updates and patches, and in some cases, for bootstrapping. As a managed service, Cloud NAT provides high availability without user management and intervention.

## Objectives

In this lab, you learn how to perform the following tasks:

- Configure a VM instance that doesn't have an external IP address
- Connect to a VM instance using an Identity-Aware Proxy (IAP) tunnel
- Enable Private Google Access on a subnet
- Configure a Cloud NAT gateway
- Verify access to public IP addresses of Google APIs and services and other connections to the internet

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
