terraform {
  required_providers {
    archive = {
      source = "hashicorp/archive"
    }
    aws = {
      source = "hashicorp/aws"
    }
  }
}

resource "aws_dynamodb_table" "notifications" {
  name         = "${var.name}-notifications"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

resource "aws_iam_role" "function" {
  name = "${var.name}-notification-capture"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "function" {
  name = "${var.name}-notification-capture"
  role = aws_iam_role.function.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:PutItem"]
      Resource = aws_dynamodb_table.notifications.arn
    }]
  })
}

resource "aws_iam_role_policy_attachment" "function_logging" {
  role       = aws_iam_role.function.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "archive_file" "function" {
  type        = "zip"
  source_file = "${path.module}/index.py"
  output_path = "${path.module}/notification-capture.zip"
}

resource "aws_lambda_function" "capture" {
  function_name    = "${var.name}-notification-capture"
  filename         = data.archive_file.function.output_path
  source_code_hash = data.archive_file.function.output_base64sha256
  handler          = "index.handler"
  role             = aws_iam_role.function.arn
  runtime          = "python3.13"

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.notifications.name
    }
  }
}

resource "aws_lambda_function_url" "capture" {
  function_name      = aws_lambda_function.capture.function_name
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "public_url" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.capture.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "public_url_invocation" {
  statement_id             = "AllowPublicFunctionUrlInvocation"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.capture.function_name
  principal                = "*"
  invoked_via_function_url = true
}