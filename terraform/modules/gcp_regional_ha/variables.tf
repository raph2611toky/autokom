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
  default = "e2-custom-2-4096"
}
variable "boot_disk_size_gb" {
  type        = number
  default     = 70
  description = "Taille du disque systeme des workers du MIG (Go)."
  validation {
    condition     = var.boot_disk_size_gb >= 70 && floor(var.boot_disk_size_gb) == var.boot_disk_size_gb
    error_message = "Le disque systeme doit faire au moins 70 Go."
  }
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
  default = 5
}
variable "cpu_target" {
  type    = number
  default = 0.55
  validation {
    condition     = var.cpu_target >= 0.1 && var.cpu_target <= 0.9
    error_message = "cpu_target doit etre compris entre 0.1 et 0.9."
  }
}
variable "cooldown_seconds" {
  type    = number
  default = 90
}
variable "bootstrap_mode" {
  type        = string
  default     = "validation"
  description = "validation = serveur HTTP technique, application = vrai demarrage applicatif."
  validation {
    condition     = contains(["validation", "worker", "application"], var.bootstrap_mode)
    error_message = "bootstrap_mode vaut validation, worker ou application."
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
  description = "Ajoute le proxy HTTPS et la règle globale sur le port 443. Ne change pas les DNS."
}
variable "domain_name" {
  type        = string
  default     = null
  nullable    = true
  description = "FQDN pour certificat gere GCP, exemple www.exemple.mg."
}
variable "https_dns_zone" {
  type        = string
  default     = null
  description = "Suffixe DNS du certificat wildcard Certificate Manager, sans * ni point final. Préparation indépendante de enable_https."
  validation {
    condition     = var.https_dns_zone == null ? true : length(var.https_dns_zone) <= 251 && can(regex("^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,63}$", var.https_dns_zone))
    error_message = "https_dns_zone doit être un suffixe DNS comme auto.ikom.mg, en minuscules, sans wildcard ni point final."
  }
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

variable "enable_cloud_nat" {
  type        = bool
  default     = false
  description = "Cloud NAT sortant facture pour les VM privees du sous-reseau existant; ne publie aucun port entrant."
}

variable "artifact_repository" {
  type        = string
  default     = "autokom-ha-images"
  description = "Depot Docker Artifact Registry existant et accessible par le compte des workers."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,62}$", var.artifact_repository))
    error_message = "Nom du depot Artifact Registry invalide."
  }
}


# APT/installation Docker sur images Ubuntu neuves peuvent prendre > 10 min.
# Max Google Compute Engine : 3600 secondes. Preferer une image prebakee ensuite.
variable "worker_initial_delay_seconds" {
  type        = number
  default     = 1800
  description = "Duree de grace de l'autohealing MIG pour les modes worker/application (secondes). Le mode validation conserve 360 s."
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
