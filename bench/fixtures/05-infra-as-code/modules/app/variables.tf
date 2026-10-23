variable "env" { type = string }
variable "vpc_id" { type = string }
variable "public_subnets" { type = list(string) }
variable "private_subnets" { type = list(string) }
variable "image" { type = string }
variable "desired_count" { type = number }
variable "db_endpoint" { type = string }
