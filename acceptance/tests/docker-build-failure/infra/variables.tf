variable "account_id" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "github_oidc_subject_prefix" {
  type = string
}

variable "acceptance_run_id" {
  type    = string
  default = ""
}
