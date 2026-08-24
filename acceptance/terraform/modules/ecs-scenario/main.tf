data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "github_actions_assume_role" {
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
        "repo:${var.github_repository}:environment:${var.environment}",
      ]
    }
  }
}

resource "aws_vpc" "app" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name        = var.app_name
    Environment = var.environment
    ManagedBy   = "release-pipeline-acceptance"
  }
}

resource "aws_internet_gateway" "app" {
  vpc_id = aws_vpc.app.id

  tags = {
    Name        = var.app_name
    Environment = var.environment
  }
}

resource "aws_subnet" "public" {
  count = 2

  vpc_id                  = aws_vpc.app.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index + 1)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name        = "${var.app_name}-public-${count.index + 1}"
    Environment = var.environment
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.app.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.app.id
  }

  tags = {
    Name        = "${var.app_name}-public"
    Environment = var.environment
  }
}

resource "aws_route_table_association" "public" {
  count = 2

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "alb" {
  name        = "${var.app_name}-alb"
  description = "Public ALB for release pipeline acceptance"
  vpc_id      = aws_vpc.app.id

  ingress {
    protocol    = "tcp"
    from_port   = 80
    to_port     = 80
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Environment = var.environment
  }
}

resource "aws_security_group" "ecs" {
  name        = "${var.app_name}-ecs"
  description = "ECS tasks for release pipeline acceptance"
  vpc_id      = aws_vpc.app.id

  ingress {
    protocol        = "tcp"
    from_port       = 80
    to_port         = 80
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Environment = var.environment
  }
}

resource "aws_ecr_repository" "app" {
  name                 = var.ecr_repository
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Environment = var.environment
    ManagedBy   = "release-pipeline-acceptance"
  }
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.app_name}"
  retention_in_days = 7

  tags = {
    Environment = var.environment
  }
}

resource "aws_iam_role" "ecs_execution" {
  name               = "${var.app_name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_assume.json

  tags = {
    Environment = var.environment
  }
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "ecs_task" {
  name               = "${var.app_name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_assume.json

  tags = {
    Environment = var.environment
  }
}

data "aws_iam_policy_document" "ecs_task_assume" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "github_actions" {
  name               = var.role_name
  assume_role_policy = data.aws_iam_policy_document.github_actions_assume_role.json
  description        = "Release pipeline acceptance role for ${var.environment}"

  tags = {
    Environment = var.environment
    ManagedBy   = "release-pipeline-acceptance"
  }
}

locals {
  site_bucket_arns = [
    for bucket in values(aws_s3_bucket.site) : bucket.arn
  ]
  site_object_arns = [
    for bucket in values(aws_s3_bucket.site) : "${bucket.arn}/*"
  ]
}

data "aws_iam_policy_document" "github_actions" {
  statement {
    sid       = "EcrAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "EcrRepository"
    effect = "Allow"
    actions = [
      "ecr:BatchDeleteImage",
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:DescribeRepositories",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:ListImages",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.app.arn]
  }

  statement {
    sid    = "EcsReadAndDeploy"
    effect = "Allow"
    actions = [
      "ecs:DescribeClusters",
      "ecs:DescribeServices",
      "ecs:DescribeTaskDefinition",
      "ecs:DescribeTasks",
      "ecs:ListTasks",
      "ecs:RegisterTaskDefinition",
      "ecs:TagResource",
      "ecs:UpdateService",
    ]
    resources = [
      aws_ecs_cluster.app.arn,
      "arn:aws:ecs:${var.aws_region}:${var.account_id}:service/${var.cluster_name}/${var.service_name}",
      "*",
    ]
  }

  statement {
    sid       = "PassTaskRoles"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.ecs_execution.arn, aws_iam_role.ecs_task.arn]
  }

  statement {
    sid       = "UpdateImagePointer"
    effect    = "Allow"
    actions   = ["ssm:GetParameter", "ssm:PutParameter"]
    resources = [aws_ssm_parameter.image.arn]
  }

  dynamic "statement" {
    for_each = length(local.site_bucket_arns) > 0 ? [true] : []

    content {
      sid    = "S3Artifacts"
      effect = "Allow"
      actions = [
        "s3:DeleteObject",
        "s3:GetObject",
        "s3:ListBucket",
        "s3:PutObject",
      ]
      resources = concat(local.site_bucket_arns, local.site_object_arns)
    }
  }

  statement {
    sid    = "LoadBalancerRead"
    effect = "Allow"
    actions = [
      "elasticloadbalancing:DescribeLoadBalancers",
      "elasticloadbalancing:DescribeTargetGroups",
      "elasticloadbalancing:DescribeTargetHealth",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_actions" {
  name   = var.role_name
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_actions.json
}

resource "aws_ecs_cluster" "app" {
  name = var.cluster_name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Environment = var.environment
    ManagedBy   = "release-pipeline-acceptance"
  }
}

resource "aws_lb" "app" {
  name               = "${var.app_name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  tags = {
    Environment = var.environment
  }
}

resource "aws_lb_target_group" "app" {
  name        = "${var.app_name}-tg"
  port        = 80
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.app.id

  health_check {
    enabled             = true
    path                = "/"
    protocol            = "HTTP"
    matcher             = "200"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
  }

  tags = {
    Environment = var.environment
  }
}

resource "aws_lb_listener" "app" {
  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

resource "aws_ecs_task_definition" "app" {
  family                   = var.app_name
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = var.bootstrap_image
      essential = true
      portMappings = [
        {
          containerPort = 80
          hostPort      = 80
          protocol      = "tcp"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = {
    Environment = var.environment
    ManagedBy   = "release-pipeline-acceptance"
  }
}

resource "aws_ecs_service" "app" {
  name            = var.service_name
  cluster         = aws_ecs_cluster.app.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  propagate_tags  = "SERVICE"

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  health_check_grace_period_seconds = 30

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "app"
    container_port   = 80
  }

  depends_on = [aws_lb_listener.app]

  tags = {
    Environment = var.environment
    ManagedBy   = "release-pipeline-acceptance"
  }
}

resource "aws_ssm_parameter" "image" {
  name  = var.ssm_parameter_name
  type  = "String"
  value = "${aws_ecr_repository.app.repository_url}:bootstrap"

  tags = {
    Environment = var.environment
    ManagedBy   = "release-pipeline-acceptance"
  }
}

resource "aws_s3_bucket" "site" {
  for_each = var.site_bucket_names

  bucket        = each.value
  force_destroy = true

  tags = {
    Environment = var.environment
    ManagedBy   = "release-pipeline-acceptance"
  }
}

resource "aws_s3_bucket_public_access_block" "site" {
  for_each = aws_s3_bucket.site

  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "site" {
  for_each = aws_s3_bucket.site

  bucket = each.value.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  for_each = aws_s3_bucket.site

  bucket = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
