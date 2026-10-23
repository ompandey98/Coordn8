resource "aws_db_subnet_group" "this" {
  name       = "billing-${var.env}"
  subnet_ids = var.private_subnets
}

resource "aws_security_group" "db" {
  vpc_id = var.vpc_id
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [var.app_sg_id]
  }
}

resource "aws_db_instance" "this" {
  identifier              = "billing-${var.env}"
  engine                  = "postgres"
  engine_version          = "16"
  instance_class          = var.instance_class
  allocated_storage       = 50
  db_subnet_group_name    = aws_db_subnet_group.this.name
  vpc_security_group_ids  = [aws_security_group.db.id]
  backup_retention_period = 7
  deletion_protection     = true
  multi_az                = true
  username                = "billing"
  manage_master_user_password = true
}
