variable "aws_account_id" {
  description = "AWS account hosting the acceptance resources."
  type        = string
  default     = "429694360874"
}

variable "aws_region" {
  description = "AWS region hosting the acceptance resources."
  type        = string
  default     = "ca-central-1"
}

variable "github_oidc_subject_prefix" {
  description = "Immutable GitHub OIDC subject prefix allowed to assume the acceptance roles."
  type        = string
  default     = "repo:cds-snc@30166251/canadalogin-release-pipeline@1337624227"
}
