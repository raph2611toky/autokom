terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 9.0"
    }
  }
  # Etat centralise sur GCS : bucket deja cree et droits IAM verifies.
  # manage.sh init injecte le bucket via backend.hcl local (non commite).
  backend "gcs" {}
}
provider "google" {
  project = var.project_id
  region  = var.region
}
