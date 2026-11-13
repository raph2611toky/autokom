mock_provider "google" {}

variables {
  project_id                 = "autokom-mock"
  confirm_billable_resources = true
}

run "default_http" {
  command = plan
  assert {
    condition     = output.https_dns_record == null && output.https_url == null
    error_message = "Le mode HTTP par défaut ne demande aucun certificat."
  }
}

run "classic_https" {
  command = plan
  variables {
    enable_https = true
    domain_name  = "test.example.org"
  }
  assert {
    condition     = output.https_url == "https://test.example.org" && output.https_dns_record == null
    error_message = "Le mode historique à un domaine reste disponible."
  }
}

run "wildcard_preparation" {
  command = plan
  variables {
    https_dns_zone = "auto.ikom.mg"
  }
  assert {
    condition     = output.https_url == null
    error_message = "La préparation DNS ne présente pas un domaine public actif."
  }
}

run "wildcard_listener" {
  command = plan
  variables {
    https_dns_zone = "auto.ikom.mg"
    enable_https   = true
  }
  assert {
    condition     = output.https_url == null
    error_message = "Le listener ne prouve pas la bascule DNS du site."
  }
}
