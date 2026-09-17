# Lab Title Goes Here

[Go to lab at skills.google](https://skills.google/paths/77/course_templates/21/labs/622982)

## Overview

In this lab, you step into the role of a Cloud Security Engineer at Cymbal Bank to deploy and test advanced network defenses. You use Cloud IDS to simulate multiple attacks, observe how next-generation threat detection identifies malicious traffic, and implement a manual incident response to block threats at the perimeter.

![perimeter diagram](https://cdn.qwiklabs.com/ENIYqVr9SB12IcIIqVmupO%2BfT7DCqkiZdHgtHJtYDuA%3D)

## Objectives

In this lab, you learn how to perform the following tasks:

- Establish a custom Google Cloud networking footprint with VPCs, subnets, and Cloud NAT.
- Create and configure a Cloud IDS endpoint to inspect traffic for malicious activity.
- Set up a packet mirroring policy to forward internal VM traffic to Cloud IDS for deep packet inspection.
- Trigger various severity threats from a client VM, including "Bash Remote Code Execution" exploits.
- Analyze real-time threat alerts and telemetry using the Cloud IDS dashboard and Cloud Logging.
- Implement a manual incident response by creating a high-priority `DENY` firewall rule to block the attacker's IP address.
- Verify the defense by confirming that subsequent attack attempts are dropped.

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
