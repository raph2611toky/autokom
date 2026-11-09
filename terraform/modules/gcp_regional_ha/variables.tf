variable "project_id" {
  type        = string
  description = "Projet GCP hebergeant les nouveaux serveurs web, distincts de l'orchestrateur."
}
variable "region" {
  type        = string
  description = "Region du MIG regional."
}
variable "zones" {
  type        = list(string)
  description = "Deux zones distinctes, toutes situees dans region."
  validation {
    condition     = length(distinct(var.zones)) >= 2 && alltrue([for z in var.zones : startswith(z, "${var.region}-")])
    error_message = "Il faut au moins deux zones distinctes dans la region choisie."
  }
}
variable "name_prefix" {
  type    = string
  default = "autokom-ha"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,19}$", var.name_prefix)) && !endswith(var.name_prefix, "-")
    error_message = "name_prefix : de 5 a 20 caracteres minuscules/chiffres/tirets, sans tiret final."
  }
}
variable "network_name" {
  type        = string
  description = "VPC EXISTANT : le module ne modifie pas le reseau."
}
variable "subnetwork_name" {
  type        = string
  description = "Sous-reseau EXISTANT dans la region."
}
variable "machine_type" {
  type    = string
  default = "e2-small"
}
variable "boot_image" {
  type        = string
  description = "Image OS ; pour la production, preferer une image preconfiguree."
  default     = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64"
}
variable "assign_external_ip" {
  type        = bool
  default     = false
  description = "False = VMs privees ; acces sortant pour mises a jour uniquement via Cloud NAT ou autre sortie existante."
}
variable "backend_port" {
  type    = number
  default = 8080
  validation {
    condition     = var.backend_port >= 1024 && var.backend_port <= 65535
    error_message = "Le port backend doit etre compris entre 1024 et 65535."
  }
}
variable "health_check_path" {
  type    = string
  default = "/healthz"
  validation {
    condition     = startswith(var.health_check_path, "/")
    error_message = "Le chemin de sante commence par /."
  }
}
variable "min_replicas" {
  type    = number
  default = 2
  validation {
    condition     = var.min_replicas >= 2 && floor(var.min_replicas) == var.min_replicas
    error_message = "Un MIG regional haute disponibilite exige au moins deux instances."
  }
}
variable "max_replicas" {
  type    = number
  default = 4
}
variable "cpu_target" {
  type    = number
  default = 0.80
  validation {
    condition     = var.cpu_target >= 0.1 && var.cpu_target <= 0.9
    error_message = "cpu_target doit etre compris entre 0.1 et 0.9."
  }
}
variable "cooldown_seconds" {
  type    = number
  default = 180
}
variable "bootstrap_mode" {
  type        = string
  default     = "validation"
  description = "validation = serveur HTTP technique, application = vrai demarrage applicatif."
  validation {
    condition     = contains(["validation", "application"], var.bootstrap_mode)
    error_message = "bootstrap_mode vaut validation ou application."
  }
}
variable "application_startup_script" {
  type        = string
  default     = null
  nullable    = true
  description = "Contenu du startup script applicatif sans mot de passe ni token (visible dans metadata)."
}
variable "enable_https" {
  type        = bool
  default     = false
  description = "Pour activer HTTPS, faire pointer le domaine vers l'IP du LB."
}
variable "domain_name" {
  type        = string
  default     = null
  nullable    = true
  description = "FQDN pour certificat gere GCP, exemple www.exemple.mg."
}
variable "restrict_mig_ingress" {
  type        = bool
  default     = true
  description = "Refuser les flux entrants vers les seuls workers MIG, sauf le trafic Load Balancer explicitement autorise."
}

variable "labels" {
  type    = map(string)
  default = { managed_by = "terraform", component = "autokom-web" }
}
