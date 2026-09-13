# Configure an Application Load Balancer with Autoscaling

[Go to lab at skills.google](https://skills.google/paths/77/course_templates/178/labs/644564)
[Terraform Registry](https://registry.terraform.io/providers/hashicorp/google/6.50.0/docs)

## Overview

Application Load Balancing (HTTP/HTTPS) is implemented at the edge of Google's network in Google's points of presence (POP) around the world. User traffic directed to an Application Load Balancer (HTTP/HTTPS) enters the POP closest to the user and is then load-balanced over Google's global network to the closest backend that has sufficient available capacity.

In this lab, you configure an Application Load Balancer (HTTP) as shown in the diagram below. Then, you stress test the load balancer to demonstrate global load balancing and autoscaling.

[ALB Diagram](https://cdn.qwiklabs.com/NKBq1D92wmG060rS2VGC97SK2sjNdwNBvyg7VqXpw1o%3D)

## Objectives

In this lab, you learn how to perform the following tasks:

- Create a health check firewall rule
- Create a NAT configuration using Cloud Router
- Create a custom image for a web server
- Create an instance template based on the custom image
- Create two managed instance groups
- Configure an Application Load Balancer (HTTP) with IPv4 and IPv6
- Stress test an Application Load Balancer (HTTP)

