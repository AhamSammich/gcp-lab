variable "project_id" {
  description = "The project ID provided by the lab"
  type        = string
}

variable "cluster_name" {
  description = "The name of the cluster"
  type        = string
}

variable "cluster_region" {
  description = "The cluster location and node region"
  type        = string
}

variable "cluster_zone" {
  description = "The cluster zone"
  type        = string
}

variable "repo_name" {
  description = "The name of the Artifact Registry repository"
  type        = string
}
