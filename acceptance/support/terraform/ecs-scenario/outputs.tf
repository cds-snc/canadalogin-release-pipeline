output "endpoint" {
  value = "http://${aws_lb.app.dns_name}"
}

output "github_actions_role_name" {
  value = aws_iam_role.github_actions.name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}
