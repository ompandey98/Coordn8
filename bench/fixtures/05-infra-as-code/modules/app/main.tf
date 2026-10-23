resource "aws_lb" "this" {
  name               = "billing-${var.env}"
  load_balancer_type = "application"
  subnets            = var.public_subnets
  security_groups    = [aws_security_group.lb.id]
}

resource "aws_lb_target_group" "app" {
  name        = "billing-${var.env}"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"
  health_check {
    path = "/healthz"
  }
}

resource "aws_security_group" "lb" {
  vpc_id = var.vpc_id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "service" {
  vpc_id = var.vpc_id
  ingress {
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.lb.id]
  }
}

resource "aws_ecs_service" "app" {
  name            = "billing-${var.env}"
  desired_count   = var.desired_count
  launch_type     = "FARGATE"
  task_definition = aws_ecs_task_definition.app.arn
  network_configuration {
    subnets         = var.private_subnets
    security_groups = [aws_security_group.service.id]
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "app"
    container_port   = 8080
  }
}

resource "aws_ecs_task_definition" "app" {
  family                   = "billing-${var.env}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  container_definitions = jsonencode([{
    name         = "app"
    image        = var.image
    portMappings = [{ containerPort = 8080 }]
    environment  = [{ name = "DB_HOST", value = var.db_endpoint }]
  }])
}
