output "regional_mig_name" {
  description = "Nom du MIG regional."
  value       = google_compute_region_instance_group_manager.web.name
}
output "regional_mig_instance_group" {
  value = google_compute_region_instance_group_manager.web.instance_group
}
output "autoscaler_name" {
  value = google_compute_region_autoscaler.web.name
}
output "load_balancer_ip" {
  value = google_compute_global_address.lb.address
}
output "http_url" {
  value = "http://${google_compute_global_address.lb.address}"
}
output "https_url" {
  value = var.enable_https ? "https://${var.domain_name}" : null
}
output "backend_service_name" {
  value = google_compute_backend_service.web.name
}
output "instance_template" {
  value = google_compute_instance_template.web.self_link_unique
}
output "web_vm_service_account" {
  value = google_service_account.web.email
}
output "bootstrap_mode" {
  value = var.bootstrap_mode
}
