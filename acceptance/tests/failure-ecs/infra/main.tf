module "scenario" {
  source = "../../../support/terraform/ecs-scenario"

  account_id                 = var.account_id
  aws_region                 = var.aws_region
  app_name                   = "cl-acceptance-failure"
  environment                = "acceptance-failure"
  github_environment         = "acceptance-tests"
  github_oidc_subject_prefix = var.github_oidc_subject_prefix
  role_name                  = "cl-acceptance-failure-actions"
  ecr_repository             = "cl-acceptance-failure"
  cluster_name               = "cl-acceptance-failure"
  service_name               = "cl-acceptance-failure-app"
  ssm_parameter_name         = "/ecs/cl-acceptance-failure/cl-acceptance-failure-app/container-image"
  vpc_cidr                   = "10.63.0.0/16"
}

resource "terraform_data" "acceptance_cleanup" {
  triggers_replace = [var.acceptance_run_id]

  provisioner "local-exec" {
    command = <<-EOT
      "${path.root}/../../../scripts/cleanup.sh" \
        --ecr-repository cl-acceptance-failure
    EOT

    environment = {
      AWS_DEFAULT_REGION = var.aws_region
      AWS_REGION         = var.aws_region
    }
  }

  depends_on = [module.scenario]
}
