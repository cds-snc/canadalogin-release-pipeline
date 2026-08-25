from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

DEFAULT_AWS_ACCOUNT_ID = "014097726303"
DEFAULT_AWS_REGION = "ca-central-1"


SCENARIOS = {
    "standard-ecs": {
        "app_name": "cl-acceptance-standard",
        "ecr_repository": "cl-acceptance-standard",
        "cluster": "cl-acceptance-standard",
        "service": "cl-acceptance-standard-app",
        "ssm_parameter": "/release-pipeline-acceptance/standard-ecs/container-image",
    },
    "react-ecs": {
        "app_name": "cl-acceptance-react",
        "ecr_repository": "cl-acceptance-react",
        "cluster": "cl-acceptance-react",
        "service": "cl-acceptance-react-app",
        "ssm_parameter": "/release-pipeline-acceptance/react-ecs/container-image",
        "site_bucket": "cl-acceptance-react-site-{aws_account_id}",
        "artifact_bucket": "cl-acceptance-react-artifacts-{aws_account_id}",
    },
    "failure-ecs": {
        "app_name": "cl-acceptance-failure",
        "ecr_repository": "cl-acceptance-failure",
        "cluster": "cl-acceptance-failure",
        "service": "cl-acceptance-failure-app",
        "ssm_parameter": "/release-pipeline-acceptance/failure-ecs/container-image",
    },
}


class VerificationError(RuntimeError):
    pass


def scenario_for_account(
    scenario: dict[str, str], account_id: str, region: str
) -> dict[str, str]:
    resolved = {
        name: value.format(aws_account_id=account_id)
        for name, value in scenario.items()
    }
    resolved["ecr_uri"] = (
        f"{account_id}.dkr.ecr.{region}.amazonaws.com/{resolved['ecr_repository']}"
    )
    return resolved


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


def has_healthy_target(target_health: object) -> bool:
    return isinstance(target_health, list) and any(
        isinstance(item, dict)
        and isinstance(item.get("TargetHealth"), dict)
        and item["TargetHealth"].get("State") == "healthy"
        for item in target_health
    )


def verify_ecr(scenario: dict[str, str], release_sha: str) -> str:
    document = aws_json(
        "ecr",
        "describe-images",
        "--repository-name",
        scenario["ecr_repository"],
        "--image-ids",
        f"imageTag={release_sha}",
    )
    details = document.get("imageDetails")
    require(isinstance(details, list) and len(details) == 1, "The release image is missing from ECR.")
    image = details[0]
    require(isinstance(image, dict), "ECR returned an invalid image detail.")
    digest = image.get("imageDigest")
    require(isinstance(digest, str) and digest, "The release image has no ECR digest.")
    return digest


def verify_ecs(scenario: dict[str, str], release_sha: str, digest: str) -> None:
    document = aws_json(
        "ecs",
        "describe-services",
        "--cluster",
        scenario["cluster"],
        "--services",
        scenario["service"],
    )
    services = document.get("services")
    failures = document.get("failures")
    require(not failures and isinstance(services, list) and len(services) == 1, "The ECS service could not be resolved.")
    service = services[0]
    require(isinstance(service, dict), "ECS returned an invalid service.")
    require(service.get("runningCount") == service.get("desiredCount") > 0, "The ECS service is not at desired capacity.")
    require(service.get("pendingCount", 0) == 0, "The ECS service still has pending tasks.")

    deployments = service.get("deployments")
    require(isinstance(deployments, list) and len(deployments) == 1, "The ECS service has an unfinished deployment.")
    deployment = deployments[0]
    require(isinstance(deployment, dict), "ECS returned an invalid deployment.")
    require(deployment.get("status") == "PRIMARY", "The expected ECS deployment is not primary.")
    require(deployment.get("rolloutState", "COMPLETED") == "COMPLETED", "The ECS rollout did not complete.")

    task_definition_arn = deployment.get("taskDefinition")
    require(isinstance(task_definition_arn, str) and task_definition_arn, "The ECS deployment has no task definition.")
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
    app_containers = [item for item in containers if isinstance(item, dict) and item.get("name") == "app"]
    require(len(app_containers) == 1, "The ECS task definition has no unique app container.")
    require(
        app_containers[0].get("image") == f"{scenario['ecr_uri']}@{digest}",
        "The ECS task definition is not pinned to the verified ECR digest.",
    )

    target_groups = aws_json(
        "elbv2",
        "describe-target-groups",
        "--names",
        f"{scenario['app_name']}-tg",
    ).get("TargetGroups")
    require(isinstance(target_groups, list) and len(target_groups) == 1, "The ALB target group is missing.")
    target_group = target_groups[0]
    require(isinstance(target_group, dict), "The ALB target group response is invalid.")
    target_group_arn = target_group.get("TargetGroupArn")
    require(isinstance(target_group_arn, str), "The ALB target group has no ARN.")
    target_health = aws_json(
        "elbv2",
        "describe-target-health",
        "--target-group-arn",
        target_group_arn,
    ).get("TargetHealthDescriptions")
    require(
        has_healthy_target(target_health),
        "The ALB has no healthy ECS targets.",
    )

    parameter = aws_json("ssm", "get-parameter", "--name", scenario["ssm_parameter"])
    parameter_value = parameter.get("Parameter", {})
    require(
        isinstance(parameter_value, dict)
        and parameter_value.get("Value") == f"{scenario['ecr_uri']}:{release_sha}",
        "The ECS SSM image pointer does not identify the tested release.",
    )


def load_balancer_url(scenario: dict[str, str]) -> str:
    document = aws_json(
        "elbv2",
        "describe-load-balancers",
        "--names",
        f"{scenario['app_name']}-alb",
    )
    load_balancers = document.get("LoadBalancers")
    require(isinstance(load_balancers, list) and len(load_balancers) == 1, "The application ALB is missing.")
    load_balancer = load_balancers[0]
    require(isinstance(load_balancer, dict), "The application ALB response is invalid.")
    dns_name = load_balancer.get("DNSName")
    require(isinstance(dns_name, str) and dns_name, "The application ALB has no DNS name.")
    return f"http://{dns_name}/"


def verify_http_response(url: str, release_sha: str) -> None:
    last_error = "no response"
    for _ in range(12):
        try:
            with urlopen(url, timeout=10) as response:
                body = response.read().decode("utf-8", errors="replace")
                require(response.status == 200, f"Application returned HTTP {response.status}.")
                require(release_sha in body, "The application did not serve the tested release SHA.")
                return
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            last_error = str(error)
            time.sleep(5)
    raise VerificationError(f"Application endpoint did not become healthy: {last_error}")


def verify_react_site(scenario: dict[str, str], release_sha: str) -> None:
    artifact_listing = aws_json(
        "s3api",
        "list-objects-v2",
        "--bucket",
        scenario["artifact_bucket"],
        "--prefix",
        release_sha,
    )
    artifact_contents = artifact_listing.get("Contents")
    require(isinstance(artifact_contents, list) and artifact_contents, "The React artifact prefix is empty.")

    index = command(["aws", "s3", "cp", f"s3://{scenario['site_bucket']}/index.html", "-"])
    require(release_sha in index, "The deployed React index does not contain the tested release SHA.")


def verify_failure_hook() -> None:
    repository = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("RUN_ID")
    require(repository is not None and run_id is not None, "GitHub workflow context is missing.")
    output = command(
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repository}/actions/runs/{run_id}/jobs",
        ]
    )
    pages = json.loads(output)
    require(isinstance(pages, list), "GitHub returned an invalid jobs response.")
    jobs = [job for page in pages if isinstance(page, dict) for job in page.get("jobs", [])]
    steps = [step for job in jobs if isinstance(job, dict) for step in job.get("steps", []) if isinstance(step, dict)]
    require(
        any(step.get("name") == "Run health checks" and step.get("conclusion") == "failure" for step in steps),
        "The expected health-check failure was not observed.",
    )
    require(
        any(step.get("name") == "Run failure hooks" and step.get("conclusion") == "success" for step in steps),
        "The failure hook did not complete successfully.",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    scenario_name = parser.parse_args().scenario
    account_id = os.environ.get("AWS_ACCOUNT_ID") or DEFAULT_AWS_ACCOUNT_ID
    region = (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or DEFAULT_AWS_REGION
    )
    scenario = scenario_for_account(SCENARIOS[scenario_name], account_id, region)

    release_sha = os.environ.get("RELEASE_SHA", "")
    expected_result = os.environ.get("EXPECTED_RESULT", "")
    pipeline_result = os.environ.get("PIPELINE_RESULT", "")
    require(len(release_sha) == 40, "RELEASE_SHA must be a full commit SHA.")
    require(pipeline_result == expected_result, f"Expected pipeline result {expected_result!r}, got {pipeline_result!r}.")

    digest = verify_ecr(scenario, release_sha)
    verify_ecs(scenario, release_sha, digest)
    verify_http_response(load_balancer_url(scenario), release_sha)
    if scenario_name == "react-ecs":
        verify_react_site(scenario, release_sha)
    if scenario_name == "failure-ecs":
        verify_failure_hook()

    print(f"Acceptance verification passed for {scenario_name} at {release_sha}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as error:
        print(f"Verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
