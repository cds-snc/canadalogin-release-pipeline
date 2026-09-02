module "notification_capture" {
  source = "../../../support/terraform/notification-capture"

  name = "cl-acceptance-docker-build-failure"
}

module "scenario" {
  source = "../../../support/terraform/ecs-scenario"

  account_id                     = var.account_id
  aws_region                     = var.aws_region
  app_name                       = "cl-acceptance-docker-build-failure"
  environment                    = "acceptance-docker-build-failure"
  github_environment             = "acceptance-tests"
  github_oidc_subject_prefix     = var.github_oidc_subject_prefix
  role_name                      = "cl-acceptance-docker-build-failure-actions"
  ecr_repository                 = "cl-acceptance-docker-build-failure"
  cluster_name                   = "cl-acceptance-docker-build-failure"
  service_name                   = "cl-acceptance-docker-build-failure-app"
  ssm_parameter_name             = "/ecs/cl-acceptance-docker-build-failure/cl-acceptance-docker-build-failure-app/container-image"
  vpc_cidr                       = "10.63.0.0/16"
  notification_capture_table_arn = module.notification_capture.table_arn
}

resource "terraform_data" "acceptance_cleanup" {
  triggers_replace = [var.acceptance_run_id]

  provisioner "local-exec" {
    command = <<-EOT
      "${path.root}/../../../scripts/cleanup.sh" \
        --ecr-repository cl-acceptance-docker-build-failure
    EOT

    environment = {
      AWS_DEFAULT_REGION = var.aws_region
      AWS_REGION         = var.aws_region
    }
  }

  depends_on = [module.scenario]
}

output "notification_capture_url" {
  value = module.notification_capture.url
}
