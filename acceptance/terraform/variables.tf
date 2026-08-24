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

variable "github_repository" {
  description = "Repository allowed to assume the acceptance roles."
  type        = string
  default     = "cds-snc/canadalogin-release-system"
}
