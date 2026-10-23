variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "env" {
  type = string
}

variable "cidr_block" {
  type    = string
  default = "10.20.0.0/16"
}

variable "app_image" {
  type = string
}

variable "app_count" {
  type    = number
  default = 2
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}
