# billing-infra

Terraform for the billing service: a private network, a load balancer, a container service and a
managed Postgres database. One root module wires three child modules together; each
environment is one variables file.

    terraform init
    terraform plan -var-file=envs/prod.tfvars
