from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


class VerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class VerificationContext:
    resources: dict[str, str]
    release_sha: str
    account_id: str
    region: str
    expected_result: str
    pipeline_result: str
    repository: str | None
    run_id: str | None

    @classmethod
    def from_environment(cls) -> VerificationContext:
        raw_resources = os.environ.get("ACCEPTANCE_RESOURCES", "{}")
        try:
            decoded = json.loads(raw_resources)
        except json.JSONDecodeError as error:
            raise VerificationError(
                f"Acceptance resources are invalid JSON: {error}"
            ) from error
        require(
            isinstance(decoded, dict), "Acceptance resources must be a JSON object."
        )
        resources = {}
        for name, value in decoded.items():
            require(
                isinstance(name, str) and isinstance(value, str),
                "Acceptance resources must contain string values.",
            )
            resources[name] = value
        return cls(
            resources=resources,
            release_sha=os.environ.get("RELEASE_SHA", ""),
            account_id=os.environ.get("AWS_ACCOUNT_ID", "429694360874"),
            region=os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION", "ca-central-1"),
            expected_result=os.environ.get("EXPECTED_RESULT", "success"),
            pipeline_result=os.environ.get("PIPELINE_RESULT", ""),
            repository=os.environ.get("GITHUB_REPOSITORY"),
            run_id=os.environ.get("RUN_ID"),
        )


def command(arguments: list[str]) -> str:
    result = subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise VerificationError(
            f"Command failed ({result.returncode}): {' '.join(arguments)}: {detail}"
        )
    return result.stdout


def aws_json(*arguments: str) -> dict[str, object]:
    output = command(["aws", *arguments, "--output", "json"])
    try:
        value = json.loads(output)
    except json.JSONDecodeError as error:
        raise VerificationError(f"AWS returned invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise VerificationError("AWS returned a non-object JSON response")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def resource(resources: Mapping[str, str], name: str) -> str:
    value = resources.get(name)
    require(
        isinstance(value, str) and value,
        f"The verification resource {name!r} is missing.",
    )
    return value


def resolve_resources(
    resources: Mapping[str, str], account_id: str, region: str
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for name, value in resources.items():
        try:
            resolved[name] = value.format(
                aws_account_id=account_id,
                aws_region=region,
            )
        except KeyError as error:
            raise VerificationError(
                f"The verification resource {name!r} uses an unknown placeholder: {error}"
            ) from error
    resolved["ecr_uri"] = (
        f"{account_id}.dkr.ecr.{region}.amazonaws.com/"
        f"{resource(resolved, 'ecr_repository')}"
    )
    return resolved


def has_healthy_target(target_health: object) -> bool:
    return isinstance(target_health, list) and any(
        isinstance(item, dict)
        and isinstance(item.get("TargetHealth"), dict)
        and item["TargetHealth"].get("State") == "healthy"
        for item in target_health
    )


def verify_target_health(target_group_arn: str) -> None:
    for attempt in range(12):
        target_health = aws_json(
            "elbv2",
            "describe-target-health",
            "--target-group-arn",
            target_group_arn,
        ).get("TargetHealthDescriptions")
        if has_healthy_target(target_health):
            return
        if attempt < 11:
            time.sleep(5)
    raise VerificationError("The ALB has no healthy ECS targets.")


def verify_ecr(resources: Mapping[str, str], release_sha: str) -> str:
    document = aws_json(
        "ecr",
        "describe-images",
        "--repository-name",
        resource(resources, "ecr_repository"),
        "--image-ids",
        f"imageTag={release_sha}",
    )
    details = document.get("imageDetails")
    require(
        isinstance(details, list) and len(details) == 1,
        "The release image is missing from ECR.",
    )
    image = details[0]
    require(isinstance(image, dict), "ECR returned an invalid image detail.")
    digest = image.get("imageDigest")
    require(isinstance(digest, str) and digest, "The release image has no ECR digest.")
    return digest


def verify_ecs(resources: Mapping[str, str], release_sha: str, digest: str) -> None:
    document = aws_json(
        "ecs",
        "describe-services",
        "--cluster",
        resource(resources, "cluster"),
        "--services",
        resource(resources, "service"),
    )
    services = document.get("services")
    failures = document.get("failures")
    require(
        not failures and isinstance(services, list) and len(services) == 1,
        "The ECS service could not be resolved.",
    )
    service = services[0]
    require(isinstance(service, dict), "ECS returned an invalid service.")
    desired_count = service.get("desiredCount")
    running_count = service.get("runningCount")
    require(
        isinstance(desired_count, int)
        and desired_count > 0
        and isinstance(running_count, int)
        and running_count >= desired_count,
        "The ECS service is not at desired capacity.",
    )
    require(
        service.get("pendingCount", 0) == 0,
        "The ECS service still has pending tasks.",
    )

    deployments = service.get("deployments")
    require(
        isinstance(deployments, list),
        "The ECS service has no deployment information.",
    )
    primary_deployments = [
        deployment
        for deployment in deployments
        if isinstance(deployment, dict) and deployment.get("status") == "PRIMARY"
    ]
    require(
        len(primary_deployments) == 1,
        "The ECS service has no unique primary deployment.",
    )
    deployment = primary_deployments[0]
    require(isinstance(deployment, dict), "ECS returned an invalid deployment.")
    require(
        deployment.get("desiredCount") == desired_count
        and deployment.get("runningCount") == desired_count
        and deployment.get("pendingCount", 0) == 0,
        "The primary ECS deployment is not at desired capacity.",
    )
    require(
        deployment.get("status") == "PRIMARY",
        "The expected ECS deployment is not primary.",
    )
    require(
        deployment.get("rolloutState", "COMPLETED") == "COMPLETED",
        "The ECS rollout did not complete.",
    )

    task_definition_arn = deployment.get("taskDefinition")
    require(
        isinstance(task_definition_arn, str) and task_definition_arn,
        "The ECS deployment has no task definition.",
    )
    task_document = aws_json(
        "ecs",
        "describe-task-definition",
        "--task-definition",
        task_definition_arn,
    )
    task_definition = task_document.get("taskDefinition")
    require(isinstance(task_definition, dict), "The ECS task definition is missing.")
    containers = task_definition.get("containerDefinitions")
    require(isinstance(containers, list), "The ECS task definition has no containers.")
    app_containers = [
        item
        for item in containers
        if isinstance(item, dict) and item.get("name") == "app"
    ]
    require(
        len(app_containers) == 1,
        "The ECS task definition has no unique app container.",
    )
    require(
        app_containers[0].get("image") == f"{resources['ecr_uri']}@{digest}",
        "The ECS task definition is not pinned to the verified ECR digest.",
    )

    target_groups = aws_json(
        "elbv2",
        "describe-target-groups",
        "--names",
        f"{resource(resources, 'app_name')}-tg",
    ).get("TargetGroups")
    require(
        isinstance(target_groups, list) and len(target_groups) == 1,
        "The ALB target group is missing.",
    )
    target_group = target_groups[0]
    require(isinstance(target_group, dict), "The ALB target group response is invalid.")
    target_group_arn = target_group.get("TargetGroupArn")
    require(isinstance(target_group_arn, str), "The ALB target group has no ARN.")
    verify_target_health(target_group_arn)

    parameter = aws_json(
        "ssm",
        "get-parameter",
        "--name",
        resource(resources, "ssm_parameter"),
    )
    parameter_value = parameter.get("Parameter", {})
    require(
        isinstance(parameter_value, dict)
        and parameter_value.get("Value") == f"{resources['ecr_uri']}:{release_sha}",
        "The ECS SSM image pointer does not identify the tested release.",
    )


def load_balancer_url(resources: Mapping[str, str]) -> str:
    document = aws_json(
        "elbv2",
        "describe-load-balancers",
        "--names",
        f"{resource(resources, 'app_name')}-alb",
    )
    load_balancers = document.get("LoadBalancers")
    require(
        isinstance(load_balancers, list) and len(load_balancers) == 1,
        "The application ALB is missing.",
    )
    load_balancer = load_balancers[0]
    require(isinstance(load_balancer, dict), "The application ALB response is invalid.")
    dns_name = load_balancer.get("DNSName")
    require(
        isinstance(dns_name, str) and dns_name, "The application ALB has no DNS name."
    )
    return f"http://{dns_name}/"


def verify_http_response(url: str, release_sha: str) -> None:
    last_error = "no response"
    for attempt in range(12):
        try:
            with urlopen(url, timeout=10) as response:
                body = response.read().decode("utf-8", errors="replace")
                require(
                    response.status == 200,
                    f"Application returned HTTP {response.status}.",
                )
                require(
                    release_sha in body,
                    "The application did not serve the tested release SHA.",
                )
                return
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            last_error = str(error)
            if attempt < 11:
                time.sleep(5)
    raise VerificationError(
        f"Application endpoint did not become healthy: {last_error}"
    )


def verify_common(context: VerificationContext) -> dict[str, str]:
    require(len(context.release_sha) == 40, "RELEASE_SHA must be a full commit SHA.")
    require(
        context.pipeline_result == context.expected_result,
        f"Expected pipeline result {context.expected_result!r}, got {context.pipeline_result!r}.",
    )
    resources = resolve_resources(context.resources, context.account_id, context.region)
    digest = verify_ecr(resources, context.release_sha)
    verify_ecs(resources, context.release_sha, digest)
    verify_http_response(load_balancer_url(resources), context.release_sha)
    return resources


def verify_react_site(resources: Mapping[str, str], release_sha: str) -> None:
    artifact_listing = aws_json(
        "s3api",
        "list-objects-v2",
        "--bucket",
        resource(resources, "artifact_bucket"),
        "--prefix",
        release_sha,
    )
    artifact_contents = artifact_listing.get("Contents")
    require(
        isinstance(artifact_contents, list) and artifact_contents,
        "The React artifact prefix is empty.",
    )

    index = command(
        [
            "aws",
            "s3",
            "cp",
            f"s3://{resource(resources, 'site_bucket')}/index.html",
            "-",
        ]
    )
    require(
        release_sha in index,
        "The deployed React index does not contain the tested release SHA.",
    )


def verify_failure_hook(context: VerificationContext) -> None:
    require(
        context.repository is not None and context.run_id is not None,
        "GitHub workflow context is missing.",
    )
    output = command(
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            f"repos/{context.repository}/actions/runs/{context.run_id}/jobs",
        ]
    )
    pages = json.loads(output)
    require(isinstance(pages, list), "GitHub returned an invalid jobs response.")
    jobs = [
        job for page in pages if isinstance(page, dict) for job in page.get("jobs", [])
    ]
    steps = [
        step
        for job in jobs
        if isinstance(job, dict)
        for step in job.get("steps", [])
        if isinstance(step, dict)
    ]
    require(
        any(
            step.get("name") == "Validate health-check result"
            and step.get("conclusion") == "success"
            for step in steps
        ),
        "The expected health-check outcome was not validated.",
    )
    require(
        any(
            step.get("name") == "Run failure hooks"
            and step.get("conclusion") == "success"
            for step in steps
        ),
        "The failure hook did not complete successfully.",
    )


if __name__ == "__main__":
    print(
        "This module provides shared acceptance verification helpers.", file=sys.stderr
    )
    raise SystemExit(2)
