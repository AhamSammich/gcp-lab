resource "google_compute_instance" "micro_vm" {
    name = var.instance_name
    machine_type = "e2-micro"
    zone = var.instance_zone
    tags = var.tags

    boot_disk {
        initialize_params {
            image = "debian-12"
        }
    }

    network_interface {
        network = var.network
        access_config {}
    }
}
