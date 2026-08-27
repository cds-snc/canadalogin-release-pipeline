module "scenario" {
  source = "../../../support/terraform/ecs-scenario"

  account_id                 = var.account_id
  aws_region                 = var.aws_region
  app_name                   = "cl-acceptance-react"
  environment                = "acceptance-react"
  github_environment         = "acceptance-tests"
  github_oidc_subject_prefix = var.github_oidc_subject_prefix
  role_name                  = "cl-acceptance-react-actions"
  ecr_repository             = "cl-acceptance-react"
  cluster_name               = "cl-acceptance-react"
  service_name               = "cl-acceptance-react-app"
  ssm_parameter_name         = "/ecs/cl-acceptance-react/cl-acceptance-react-app/container-image"
  vpc_cidr                   = "10.62.0.0/16"
  site_bucket_names = {
    artifacts = "cl-acceptance-react-artifacts-${var.account_id}"
    site      = "cl-acceptance-react-site-${var.account_id}"
  }
}

resource "terraform_data" "acceptance_cleanup" {
  triggers_replace = [var.acceptance_run_id]

  provisioner "local-exec" {
    command = <<-EOT
      "${path.root}/../../../scripts/cleanup.sh" \
        --ecr-repository cl-acceptance-react \
        --bucket "cl-acceptance-react-artifacts-${var.account_id}" \
        --bucket "cl-acceptance-react-site-${var.account_id}"
    EOT

    environment = {
      AWS_DEFAULT_REGION = var.aws_region
      AWS_REGION         = var.aws_region
    }
  }

  depends_on = [module.scenario]
}
