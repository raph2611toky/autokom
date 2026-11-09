# Blocage de securite : sans confirmation explicite, le plan ne peut pas etre applique.
resource "terraform_data" "cost_gate" {
  input = var.confirm_billable_resources
  lifecycle {
    precondition {
      condition     = var.confirm_billable_resources
      error_message = "Cout GCP non confirme. Renseigner confirm_billable_resources=true apres evaluation des couts."
    }
  }
}

module "regional_web_ha" {
  source                = "../../modules/gcp_regional_ha"
  project_id            = var.project_id
  region                = var.region
  zones                 = var.zones
  network_name          = var.network_name
  subnetwork_name       = var.subnetwork_name
  name_prefix           = var.name_prefix
  machine_type          = var.machine_type
  boot_image            = var.boot_image
  assign_external_ip    = var.assign_external_ip
  backend_port          = var.backend_port
  health_check_path     = var.health_check_path
  min_replicas          = var.min_replicas
  max_replicas          = var.max_replicas
  cpu_target            = var.cpu_target
  restrict_mig_ingress  = var.restrict_mig_ingress
  cooldown_seconds      = var.cooldown_seconds
  bootstrap_mode        = var.bootstrap_mode
  application_startup_script = trimspace(var.application_startup_script_file) != "" ? file(abspath(var.application_startup_script_file)) : null
  enable_https          = var.enable_https
  domain_name           = var.domain_name
  depends_on            = [terraform_data.cost_gate]
}
