output "terraform_role_name" {
  value = aws_iam_role.terraform.name
}

output "scenario_endpoints" {
  value = {
    standard = module.standard.endpoint
    react    = module.react.endpoint
    failure  = module.failure.endpoint
  }
}

output "scenario_roles" {
  value = {
    standard = module.standard.github_actions_role_name
    react    = module.react.github_actions_role_name
    failure  = module.failure.github_actions_role_name
  }
}
