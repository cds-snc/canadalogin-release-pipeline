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
        "${var.github_oidc_subject_prefix}:environment:acceptance-tests",
      ]
    }
  }
}

resource "aws_iam_role" "terraform" {
  name               = "cl-acceptance-terraform"
  assume_role_policy = data.aws_iam_policy_document.terraform_assume_role.json
  description        = "Terraform role for release pipeline acceptance infrastructure"
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
