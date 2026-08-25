data "aws_caller_identity" "current" {}

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "terraform_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_repository}:environment:acceptance-tests",
      ]
    }
  }
}

resource "aws_iam_role" "terraform" {
  name               = "cl-acceptance-terraform"
  assume_role_policy = data.aws_iam_policy_document.terraform_assume_role.json
  description        = "Scratch-only Terraform role for release pipeline acceptance infrastructure"
}

data "aws_iam_policy_document" "terraform" {
  statement {
    sid       = "AcceptanceInfrastructure"
    effect    = "Allow"
    actions   = ["ec2:*", "ecs:*", "ecr:*", "elasticloadbalancing:*", "logs:*"]
    resources = ["*"]
  }

  statement {
    sid       = "AcceptanceStorage"
    effect    = "Allow"
    actions   = ["s3:*", "dynamodb:*", "ssm:*", "cloudwatch:*"]
    resources = ["*"]
  }

  statement {
    sid       = "AcceptanceIam"
    effect    = "Allow"
    actions   = ["iam:*", "sts:GetCallerIdentity"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "terraform" {
  name   = "cl-acceptance-terraform"
  role   = aws_iam_role.terraform.id
  policy = data.aws_iam_policy_document.terraform.json
}

module "standard" {
  source = "./modules/ecs-scenario"

  account_id         = var.aws_account_id
  aws_region         = var.aws_region
  app_name           = "cl-acceptance-standard"
  environment        = "acceptance-standard"
  github_environment = "acceptance-tests"
  github_repository  = var.github_repository
  role_name          = "cl-acceptance-standard-actions"
  ecr_repository     = "cl-acceptance-standard"
  cluster_name       = "cl-acceptance-standard"
  service_name       = "cl-acceptance-standard-app"
  ssm_parameter_name = "/release-pipeline-acceptance/standard-ecs/container-image"
  vpc_cidr           = "10.61.0.0/16"
}

module "react" {
  source = "./modules/ecs-scenario"

  account_id         = var.aws_account_id
  aws_region         = var.aws_region
  app_name           = "cl-acceptance-react"
  environment        = "acceptance-react"
  github_environment = "acceptance-tests"
  github_repository  = var.github_repository
  role_name          = "cl-acceptance-react-actions"
  ecr_repository     = "cl-acceptance-react"
  cluster_name       = "cl-acceptance-react"
  service_name       = "cl-acceptance-react-app"
  ssm_parameter_name = "/release-pipeline-acceptance/react-ecs/container-image"
  vpc_cidr           = "10.62.0.0/16"
  site_bucket_names = {
    artifacts = "cl-acceptance-react-artifacts-${var.aws_account_id}"
    site      = "cl-acceptance-react-site-${var.aws_account_id}"
  }
}

module "failure" {
  source = "./modules/ecs-scenario"

  account_id         = var.aws_account_id
  aws_region         = var.aws_region
  app_name           = "cl-acceptance-failure"
  environment        = "acceptance-failure"
  github_environment = "acceptance-tests"
  github_repository  = var.github_repository
  role_name          = "cl-acceptance-failure-actions"
  ecr_repository     = "cl-acceptance-failure"
  cluster_name       = "cl-acceptance-failure"
  service_name       = "cl-acceptance-failure-app"
  ssm_parameter_name = "/release-pipeline-acceptance/failure-ecs/container-image"
  vpc_cidr           = "10.63.0.0/16"
}
