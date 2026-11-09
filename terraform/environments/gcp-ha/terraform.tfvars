# Parametres du projet GCP confirme dans nos anciens diagnostics.
project_id      = "plucky-stacker-480421-c4"
region          = "us-central1"
zones           = ["us-central1-a", "us-central1-f"]
network_name    = "default"          # Verifier le vrai reseau et le sous-reseau
subnetwork_name = "default"
name_prefix     = "autokom-ha"

# Les instances du MIG sont NOUVELLES
machine_type       = "e2-small"
assign_external_ip = false
min_replicas       = 2
max_replicas       = 4
cpu_target         = 0.80
cooldown_seconds   = 180
backend_port       = 8080
health_check_path  = "/healthz"

bootstrap_mode                 = "validation"
application_startup_script_file = ""

enable_https = false
domain_name  = null

confirm_billable_resources = false
