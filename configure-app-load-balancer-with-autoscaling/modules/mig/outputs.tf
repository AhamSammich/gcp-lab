output "manager_self_link" {
  description = "The created instance group manager"
  value = google_compute_region_instance_group_manager.mig.self_link
}

output "instance_group" {
  description = "The created instance group"
  value = google_compute_region_instance_group_manager.mig.instance_group
}
