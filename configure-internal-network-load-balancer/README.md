# Configure an Internal Network Load Balancer

[Go to lab at skills.google](https://www.skills.google/paths/77/course_templates/178/labs/644569)

## Overview

Google Cloud offers internal Network Load Balancing for your TCP/UDP-based traffic. Internal Network Load Balancing enables you to run and scale your services behind a private load balancing IP address that is accessible only to your internal virtual machine instances.

In this lab, you create two managed instance groups in the same region. Then you configure and test an internal Network Load Balancer with the instances groups as the backends, as shown in this network diagram:

![Network Diagram](https://cdn.qwiklabs.com/FgrZkcSEqghVxKV14KPVgNzTUMo0lQfmqTgpG45%2BTYA%3D)

## Objectives

In this lab, you will learn how to perform the following tasks:

- Create internal traffic and health check firewall rules.
- Create a NAT configuration using Cloud Router.
- Configure two instance templates.
- Create two managed instance groups.
- Configure and test an internal Network Load Balancer.
