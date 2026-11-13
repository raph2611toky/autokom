output "mig_regional" {
  value = module.regional_web_ha.regional_mig_name
}
output "autoscaler" {
  value = module.regional_web_ha.autoscaler_name
}
output "load_balancer_public_ip" {
  value = module.regional_web_ha.load_balancer_ip
}
output "validation_url_http" {
  value = module.regional_web_ha.http_url
}
output "https_url" {
  value = module.regional_web_ha.https_url
}
output "https_dns_record" {
  value = module.regional_web_ha.https_dns_record
}
output "web_vm_service_account" {
  value = module.regional_web_ha.web_vm_service_account
}
output "instance_template" {
  value = module.regional_web_ha.instance_template
}
output "deployment_mode" {
  value = module.regional_web_ha.bootstrap_mode
}
