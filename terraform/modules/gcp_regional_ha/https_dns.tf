# Prepare TLS without changing A/AAAA records or enabling the public cutover.
resource "google_certificate_manager_dns_authorization" "web" {
  count    = var.https_dns_zone != null ? 1 : 0
  project  = var.project_id
  location = "global"
  name     = "${var.name_prefix}-dns-auth"
  domain   = var.https_dns_zone
  type     = "PER_PROJECT_RECORD"
}

resource "google_certificate_manager_certificate" "web" {
  count    = var.https_dns_zone != null ? 1 : 0
  project  = var.project_id
  location = "global"
  name     = "${var.name_prefix}-wildcard"
  managed {
    domains            = ["*.${var.https_dns_zone}"]
    dns_authorizations = [google_certificate_manager_dns_authorization.web[0].id]
  }
}

resource "google_certificate_manager_certificate_map" "web" {
  count   = var.https_dns_zone != null ? 1 : 0
  project = var.project_id
  name    = "${var.name_prefix}-tls-map"
}

resource "google_certificate_manager_certificate_map_entry" "web_wildcard" {
  count        = var.https_dns_zone != null ? 1 : 0
  project      = var.project_id
  name         = "${var.name_prefix}-wildcard-entry"
  map          = google_certificate_manager_certificate_map.web[0].name
  certificates = [google_certificate_manager_certificate.web[0].id]
  hostname     = "*.${var.https_dns_zone}"
}
