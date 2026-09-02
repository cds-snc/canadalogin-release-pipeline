output "table_arn" {
  value = aws_dynamodb_table.notifications.arn
}

output "url" {
  value = aws_lambda_function_url.capture.function_url
}