module "scenario" {
  source = "../../../support/terraform/ecs-scenario"

  account_id                 = var.account_id
  aws_region                 = var.aws_region
  app_name                   = "cl-acceptance-standard"
  environment                = "acceptance-standard"
  github_environment         = "acceptance-tests"
  github_oidc_subject_prefix = var.github_oidc_subject_prefix
  role_name                  = "cl-acceptance-standard-actions"
  ecr_repository             = "cl-acceptance-standard"
  cluster_name               = "cl-acceptance-standard"
  service_name               = "cl-acceptance-standard-app"
  ssm_parameter_name         = "/ecs/cl-acceptance-standard/cl-acceptance-standard-app/container-image"
  vpc_cidr                   = "10.61.0.0/16"
}

resource "terraform_data" "acceptance_cleanup" {
  triggers_replace = [var.acceptance_run_id]

  provisioner "local-exec" {
    command = <<-EOT
      "${path.root}/../../../scripts/cleanup.sh" \
        --ecr-repository cl-acceptance-standard
    EOT

    environment = {
      AWS_DEFAULT_REGION = var.aws_region
      AWS_REGION         = var.aws_region
    }
  }

  depends_on = [module.scenario]
}
