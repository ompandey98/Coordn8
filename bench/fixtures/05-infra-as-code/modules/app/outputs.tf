output "lb_dns_name" {
  value = aws_lb.this.dns_name
}

output "service_sg_id" {
  value = aws_security_group.service.id
}
