data "google_compute_network" "existing" {
  project = var.project_id
  name    = var.network_name
}

data "google_compute_subnetwork" "existing" {
  project = var.project_id
  region  = var.region
  name    = var.subnetwork_name
}

# Identite sans permission implicite : les roles applicatifs se donnent explicitement.
resource "google_service_account" "web" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-vm"
  display_name = "AUTOKOM regional MIG web instances"
}

resource "google_compute_instance_template" "web" {
  project      = var.project_id
  name_prefix  = "${var.name_prefix}-tpl-"
  description  = "Only AUTOKOM web workers; NOT the main AUTOKOM orchestrator."
  machine_type = var.machine_type
  tags         = ["${var.name_prefix}-backend"]
  labels       = var.labels

  disk {
    boot         = true
    auto_delete  = true
    source_image = var.boot_image
    disk_size_gb = 20
    disk_type    = "pd-balanced"
  }

  network_interface {
    subnetwork = data.google_compute_subnetwork.existing.self_link
    dynamic "access_config" {
      for_each = var.assign_external_ip ? [1] : []
      content {}
    }
  }

  service_account {
    email  = google_service_account.web.email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  metadata_startup_script = var.bootstrap_mode == "validation" ? templatefile("${path.module}/files/validation-startup.sh.tftpl", {
    backend_port      = var.backend_port
    health_check_path = var.health_check_path
  }) : var.application_startup_script

  lifecycle {
    create_before_destroy = true
    precondition {
      condition     = var.bootstrap_mode == "validation" || (var.application_startup_script != null && trimspace(var.application_startup_script) != "")
      error_message = "bootstrap_mode=application exige application_startup_script, sans secret."
    }
  }
}

# La regle cible EXCLUSIVEMENT les nouveaux workers du MIG, et non l'orchestrateur.
resource "google_compute_firewall" "lb_to_web" {
  project       = var.project_id
  name          = "${var.name_prefix}-allow-lb"
  description   = "Google health checks et proxies globaux vers le backend web."
  network       = data.google_compute_network.existing.id
  direction     = "INGRESS"
  priority      = 1000
  source_ranges = ["35.191.0.0/16", "130.211.0.0/22"]
  target_tags   = ["${var.name_prefix}-backend"]

  allow {
    protocol = "tcp"
    ports    = [tostring(var.backend_port)]
  }
}

resource "google_compute_health_check" "web" {
  project             = var.project_id
  name                = "${var.name_prefix}-health"
  check_interval_sec  = 10
  timeout_sec         = 5
  healthy_threshold   = 2
  unhealthy_threshold = 3

  http_health_check {
    port         = var.backend_port
    request_path = var.health_check_path
  }
}

resource "google_compute_region_instance_group_manager" "web" {
  project                   = var.project_id
  region                    = var.region
  name                      = "${var.name_prefix}-rmig"
  base_instance_name        = "${var.name_prefix}-web"
  distribution_policy_zones = var.zones
  target_size               = var.min_replicas
  deletion_policy           = "PREVENT"

  version {
    instance_template = google_compute_instance_template.web.self_link_unique
  }

  named_port {
    name = "http"
    port = var.backend_port
  }

  auto_healing_policies {
    health_check      = google_compute_health_check.web.id
    initial_delay_sec = 360
  }

  lifecycle {
    # L'Autoscaler, pas Terraform, gere la taille apres creation.
    ignore_changes  = [target_size]
    prevent_destroy = true
  }
  depends_on = [google_compute_firewall.lb_to_web]
}

resource "google_compute_region_autoscaler" "web" {
  project = var.project_id
  region  = var.region
  name    = "${var.name_prefix}-autoscaler"
  target  = google_compute_region_instance_group_manager.web.id

  autoscaling_policy {
    min_replicas    = var.min_replicas
    max_replicas    = var.max_replicas
    cooldown_period = var.cooldown_seconds

    cpu_utilization {
      target = var.cpu_target
    }
  }
  lifecycle {
    precondition {
      condition     = var.max_replicas >= var.min_replicas
      error_message = "max_replicas doit etre >= min_replicas."
    }
  }
}

# Global external Application Load Balancer (HTTP valide en premiere phase).
resource "google_compute_global_address" "lb" {
  project      = var.project_id
  name         = "${var.name_prefix}-lb-ip"
  address_type = "EXTERNAL"
  ip_version   = "IPV4"
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_compute_backend_service" "web" {
  project               = var.project_id
  name                  = "${var.name_prefix}-backend"
  protocol              = "HTTP"
  port_name             = "http"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  timeout_sec           = 30
  health_checks         = [google_compute_health_check.web.id]
  session_affinity      = "NONE"

  backend {
    group           = google_compute_region_instance_group_manager.web.instance_group
    balancing_mode  = "UTILIZATION"
    max_utilization = 0.8
  }
}

resource "google_compute_url_map" "web" {
  project         = var.project_id
  name            = "${var.name_prefix}-map"
  default_service = google_compute_backend_service.web.id
}

resource "google_compute_target_http_proxy" "web" {
  project = var.project_id
  name    = "${var.name_prefix}-http-proxy"
  url_map = google_compute_url_map.web.id
}

resource "google_compute_global_forwarding_rule" "http" {
  project               = var.project_id
  name                  = "${var.name_prefix}-http"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_protocol           = "TCP"
  port_range            = "80"
  ip_address            = google_compute_global_address.lb.id
  target                = google_compute_target_http_proxy.web.id
}

# HTTPS facultatif : certificat emis uniquement si DNS pointe deja sur l'IP ci-dessus.
resource "google_compute_managed_ssl_certificate" "web" {
  count   = var.enable_https ? 1 : 0
  project = var.project_id
  name    = "${var.name_prefix}-cert"
  managed {
    domains = [var.domain_name]
  }
  lifecycle {
    precondition {
      condition     = var.domain_name != null && trimspace(var.domain_name == null ? "" : var.domain_name) != ""
      error_message = "enable_https=true exige un nom de domaine DNS valide dans domain_name."
    }
  }
}

resource "google_compute_target_https_proxy" "web" {
  count            = var.enable_https ? 1 : 0
  project          = var.project_id
  name             = "${var.name_prefix}-https-proxy"
  url_map          = google_compute_url_map.web.id
  ssl_certificates = [google_compute_managed_ssl_certificate.web[0].id]
}

resource "google_compute_global_forwarding_rule" "https" {
  count                 = var.enable_https ? 1 : 0
  project               = var.project_id
  name                  = "${var.name_prefix}-https"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_protocol           = "TCP"
  port_range            = "443"
  ip_address            = google_compute_global_address.lb.id
  target                = google_compute_target_https_proxy.web[0].id
}
