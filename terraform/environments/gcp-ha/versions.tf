terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 9.0"
    }
  }
  # Etat local pour le premier controle; pour utilisation equipee, migrer vers GCS
  # (bucket cree et protege au prealable) AVANT tout terraform apply partage.
}
provider "google" {
  project = var.project_id
  region  = var.region
}
