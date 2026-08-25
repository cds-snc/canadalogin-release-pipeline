variable "account_id" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "app_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "github_environment" {
  type = string
}

variable "github_repository" {
  type = string
}

variable "role_name" {
  type = string
}

variable "ecr_repository" {
  type = string
}

variable "cluster_name" {
  type = string
}

variable "service_name" {
  type = string
}

variable "ssm_parameter_name" {
  type = string
}

variable "vpc_cidr" {
  type = string
}

variable "bootstrap_image" {
  type    = string
  default = "public.ecr.aws/docker/library/nginx:1.27-alpine"
}

variable "site_bucket_names" {
  type    = map(string)
  default = {}
}
