variable "aws_account_id" {
  description = "Scratch account hosting the acceptance resources."
  type        = string
  default     = "014097726303"
}

variable "aws_region" {
  description = "AWS region hosting the acceptance resources."
  type        = string
  default     = "ca-central-1"
}

variable "github_oidc_subject_prefix" {
  description = "Immutable GitHub OIDC subject prefix allowed to assume the acceptance roles."
  type        = string
  default     = "repo:cds-snc@30166251/canadalogin-release-system@1337624227"
}

variable "acceptance_run_id" {
  description = "Unique acceptance run identifier used to clear per-run artifacts."
  type        = string
  default     = ""
}
