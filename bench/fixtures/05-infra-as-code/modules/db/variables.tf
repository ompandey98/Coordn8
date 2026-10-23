variable "env" { type = string }
variable "vpc_id" { type = string }
variable "private_subnets" { type = list(string) }
variable "app_sg_id" { type = string }
variable "instance_class" { type = string }
