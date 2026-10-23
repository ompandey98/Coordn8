output "public_url" {
  value = "https://${module.app.lb_dns_name}"
}

output "db_endpoint" {
  value     = module.db.endpoint
  sensitive = true
}
