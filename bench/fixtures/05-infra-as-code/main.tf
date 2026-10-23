module "network" {
  source     = "./modules/network"
  env        = var.env
  cidr_block = var.cidr_block
}

module "app" {
  source          = "./modules/app"
  env             = var.env
  vpc_id          = module.network.vpc_id
  public_subnets  = module.network.public_subnet_ids
  private_subnets = module.network.private_subnet_ids
  image           = var.app_image
  desired_count   = var.app_count
  db_endpoint     = module.db.endpoint
}

module "db" {
  source          = "./modules/db"
  env             = var.env
  vpc_id          = module.network.vpc_id
  private_subnets = module.network.private_subnet_ids
  app_sg_id       = module.app.service_sg_id
  instance_class  = var.db_instance_class
}
