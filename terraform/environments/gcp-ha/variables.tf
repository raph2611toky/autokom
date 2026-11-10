variable "project_id" {
  type = string
}
variable "region" {
  type    = string
  default = "us-central1"
}
variable "zones" {
  type    = list(string)
  default = ["us-central1-a", "us-central1-f"]
}
variable "network_name" {
  type    = string
  default = "default"
}
variable "subnetwork_name" {
  type    = string
  default = "default"
}
variable "name_prefix" {
  type    = string
  default = "autokom-ha"
}
variable "machine_type" {
  type    = string
  default = "e2-small"
}
variable "boot_image" {
  type    = string
  default = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64"
}
variable "assign_external_ip" {
  type    = bool
  default = false
}
variable "backend_port" {
  type    = number
  default = 8080
}
variable "health_check_path" {
  type    = string
  default = "/healthz"
}
variable "min_replicas" {
  type    = number
  default = 2
}
variable "max_replicas" {
  type    = number
  default = 4
}
variable "cpu_target" {
  type    = number
  default = 0.80
}
variable "cooldown_seconds" {
  type    = number
  default = 180
}
variable "bootstrap_mode" {
  type    = string
  default = "validation"
}
variable "application_startup_script_file" {
  type        = string
  default     = ""
  description = "Chemin d'un script de demarrage applicatif ; jamais de secret dans metadata."
}
variable "enable_https" {
  type    = bool
  default = false
}
variable "domain_name" {
  type     = string
  default  = null
  nullable = true
}
variable "restrict_mig_ingress" {
  type        = bool
  default     = true
  description = "Bloquer les autres flux entrants aux seules nouvelles VM du MIG; n'affecte pas les VM existantes."
}

variable "confirm_billable_resources" {
  type        = bool
  default     = false
  description = "Doit etre true pour autoriser un plan et un deploiement des ressources GCP payantes."
}

variable "enable_cloud_nat" {
  type        = bool
  default     = false
  description = "Activer explicitement Cloud NAT facturable pour les workers sans IP externe (sous-reseau default)."
}

variable "artifact_repository" {
  type        = string
  default     = "autokom-ha-images"
  description = "Depot Docker regional utilisable par les workers en mode worker."
}


variable "worker_initial_delay_seconds" {
  type        = number
  default     = 1800
  description = "Initialisation des VM worker/applications sans recreation prematuree (360 a 3600 s)."
  validation {
    condition     = var.worker_initial_delay_seconds >= 360 && var.worker_initial_delay_seconds <= 3600 && floor(var.worker_initial_delay_seconds) == var.worker_initial_delay_seconds
    error_message = "worker_initial_delay_seconds doit etre un entier entre 360 et 3600 secondes."
  }
}

variable "enable_app_delivery" {
  description = "Install private GCS state, immutable-image agent and application ingress on MIG workers."
  type        = bool
  default     = false
}
