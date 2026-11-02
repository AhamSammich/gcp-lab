# Create a Landing Zone and Harden it with Security Command Centre: Challenge Lab

[Go to lab at skills.google](https://partner.skills.google/course_templates/1573/labs/630245)

## Challenge Lab Overview

In this lab, you deploy a secure, production-ready landing zone using the Google Cloud foundation - the fundamental bedrock of your environment that establishes the necessary resource hierarchy, security guardrails, and network architecture before you even deploy an application.

To build this, you utilize Terraform as your 'infrastructure as code' engine. You will configure a Shared VPC with Private Google Access and a VM instance using Google best practice modules; these are pre-packaged Terraform code blocks that automate the deployment to ensure your infrastructure is consistent, repeatable, and adheres to Google's architectural standards without manual error.

Additionally, you enforce security best practices, such as blocking serial ports and denying external IP addresses, and enable Security Command Center to monitor the environment.

When you take a Challenge Lab, you will not be taught Google Cloud concepts. You will need to use your skills to assess how to build the solution to the challenge presented. This lab is only recommended for students who have those skills. Are you up for the challenge?

## Objective

In this lab, you will demonstrate proficiency in deploying and securing a Google Cloud environment using a set of pre-configured resources. The Host project and Service project have already been created for you. You will complete the following tasks:

- Deploy a Shared VPC in a Host Project using Terraform Cloud Foundation Fabric modules stored in the Cloud Storage bucket named qwiklabs-gcp-04-c8652fc9ca83-labconfig-bucket and enable Private Google Access on the subnets.
- Enforce Organization Policies to secure the environment.
- Deploy a standard VM instance with no external IP address.
- Enable Security Command Center.

## Challenge Scenario

![cymbal_shops](https://cdn.qwiklabs.com/1dLgf%2Bq6klWZgZPna%2FMj92WmJkV3z92NKez4JsVEyuY%3D)

Here is a company overview as provided on the Cymbal Bank's website.

Cymbal Bank is an American retail bank with over 2,000 branches in all 50 states. It offers comprehensive debit and credit services that are built on top of a robust payments platform. Cymbal Bank was founded in 1920 under the name Troxler. Cymbal Group acquired the company in 1975 after it had been investing heavily in Cymbal Group’s proprietary ATMs. As the bank grew into a national leader, they put strategic emphasis on modernizing the customer experience both in-person at their branches and digitally through an app they released in 2014. Cymbal Bank employs 42,000 people nationwide and, in 2019, reported $24 billion in revenue. Cymbal Bank is a digitally transforming legacy financial services institution. It is inspired by clients like: Key Bank, Capital One

### Your challenge

Your task is to build this secure foundation using Google's best-practice infrastructure modules. This environment must be secure by default, centrally managed, and ready to host the bank's first pilot application. Throughout this lab, you use repeatable, Google-vetted IaC models from the Cloud Foundation Fabric library to deploy and secure this landing zone. Your tasks include:

![workflow](https://cdn.qwiklabs.com/obzFMdKlheczJEIEVaoKA8M6AmYgMbApW917PE%2Bvh6E%3D)

- Configuring a Shared VPC: Attach the development and production projects as service projects to allow them to use centralized subnets.
- Enabling Private Google Access: Configure the Shared VPC subnet to ensure internal VMs can access Google APIs (like Cloud Storage or BigQuery) without public IP addresses.
- Configuring Secure Access: Create firewall rules that deny all external SSH traffic and only allow TCP traffic from the Identity-Aware Proxy (IAP) IP range.
- Deploying a Hardened VM: Use a provided module to launch a VM into the development project's Shared VPC subnet. You must ensure the VM has no external IP address and that serial port access is disabled via metadata.
- Activating Security Command Center: Enable the Standard Tier at the organization level to provide the security team with centralized visibility into assets and potential misconfigurations.

Throughout the lab, you will need to use these project IDs and the specified region to create and manage resources.

Field Value Host Project ID qwiklabs-gcp-04-c8652fc9ca83 Service Project ID qwiklabs-gcp-04-fbe58af5f804 Region us-east1

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