from __future__ import annotations

import copy
import json
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .commands import CommandRunner
from .config import (
    DEPLOYMENT_WORKFLOW_SECRETS,
    ConfigError,
    DeploymentConfig,
    EcsServiceConfig,
    PipelineConfig,
)
from .runtime import RuntimeContext, render, render_s3_prefix, resolve_reference

REGISTER_TASK_DEFINITION_FIELDS = {
    "containerDefinitions",
    "cpu",
    "enableFaultInjection",
    "ephemeralStorage",
    "executionRoleArn",
    "family",
    "inferenceAccelerators",
    "ipcMode",
    "memory",
    "networkMode",
    "pidMode",
    "placementConstraints",
    "proxyConfiguration",
    "requiresCompatibilities",
    "runtimePlatform",
    "taskRoleArn",
    "volumes",
}


@dataclass(frozen=True)
class DeploymentResult:
    deployment_sha: str
    changed_resources: tuple[str, ...]
    unchanged_resources: tuple[str, ...]

    def github_outputs(self) -> dict[str, str]:
        return {
            "deployment_sha": self.deployment_sha,
            "changed_resources": json.dumps(
                self.changed_resources, separators=(",", ":")
            ),
            "unchanged_resources": json.dumps(
                self.unchanged_resources, separators=(",", ":")
            ),
        }


@dataclass(frozen=True)
class _S3Target:
    bucket: str
    delete: bool


@dataclass(frozen=True)
class _CloudFrontInvalidation:
    distribution: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class _S3Operation:
    name: str
    artifact_uri: str
    targets: tuple[_S3Target, ...]
    invalidations: tuple[_CloudFrontInvalidation, ...]


@dataclass(frozen=True)
class _EcsState:
    deployment: DeploymentConfig
    service_config: EcsServiceConfig
    cluster: str
    service: str
    container: str
    desired_image: str
    current_image: str
    task_definition_arn: str
    task_definition: Mapping[str, Any]
    ssm_parameter: str | None


def target_context(config: PipelineConfig, context: RuntimeContext) -> RuntimeContext:
    if context.environment not in config.environments.deploy:
        raise ConfigError(f"Environment {context.environment!r} is not deployable")
    if not context.sha:
        raise ConfigError("The planned deployment SHA is empty")
    return context


def deploy_s3(
    config: PipelineConfig,
    context: RuntimeContext,
    *,
    runner: CommandRunner | None = None,
) -> DeploymentResult:
    runner = runner or CommandRunner()
    context = target_context(config, context)
    operations = _prepare_s3(config, context, runner)
    changed: list[str] = []
    for operation in operations:
        for target in operation.targets:
            command = [
                "aws",
                "s3",
                "sync",
                operation.artifact_uri,
                f"s3://{target.bucket}",
            ]
            if target.delete:
                command.append("--delete")
            _run_aws(runner, command)
            changed.append(f"s3://{target.bucket}")
        for invalidation in operation.invalidations:
            _run_aws(
                runner,
                [
                    "aws",
                    "cloudfront",
                    "create-invalidation",
                    "--distribution-id",
                    invalidation.distribution,
                    "--paths",
                    *invalidation.paths,
                ],
            )
            changed.append(f"cloudfront:{invalidation.distribution}")
    return DeploymentResult(context.sha, tuple(changed), ())


def preflight_s3(
    config: PipelineConfig,
    context: RuntimeContext,
    *,
    runner: CommandRunner | None = None,
) -> DeploymentResult:
    runner = runner or CommandRunner()
    context = target_context(config, context)
    _prepare_s3(config, context, runner)
    return DeploymentResult(context.sha, (), ())


def deploy_ecs(
    config: PipelineConfig,
    context: RuntimeContext,
    *,
    force_redeploy: bool = False,
    runner: CommandRunner | None = None,
) -> DeploymentResult:
    runner = runner or CommandRunner()
    context = target_context(config, context)
    states = _prepare_ecs(config, context, runner)
    changed: list[str] = []
    unchanged: list[str] = []
    for state in states:
        resource = f"ecs:{state.cluster}/{state.service}"
        if state.current_image == state.desired_image and not force_redeploy:
            print(
                f"{resource} already runs {state.desired_image}; skipping deployment."
            )
            _wait_for_service_stability(state, runner)
            _update_ssm(state, runner)
            unchanged.append(resource)
            continue

        task_definition_arn = state.task_definition_arn
        if state.current_image != state.desired_image:
            task_definition_arn = _register_task_definition(state, runner)

        update_command = [
            "aws",
            "ecs",
            "update-service",
            "--cluster",
            state.cluster,
            "--service",
            state.service,
        ]
        if state.current_image == state.desired_image:
            update_command.append("--force-new-deployment")
        else:
            update_command.extend(["--task-definition", task_definition_arn])
        update_command.extend(["--propagate-tags", "SERVICE"])
        _run_aws(runner, update_command)
        _wait_for_service_stability(state, runner)
        _update_ssm(state, runner)
        changed.append(resource)
    return DeploymentResult(context.sha, tuple(changed), tuple(unchanged))


def _wait_for_service_stability(state: _EcsState, runner: CommandRunner) -> None:
    _run_aws(
        runner,
        [
            "aws",
            "ecs",
            "wait",
            "services-stable",
            "--cluster",
            state.cluster,
            "--services",
            state.service,
        ],
    )


def preflight_ecs(
    config: PipelineConfig,
    context: RuntimeContext,
    *,
    runner: CommandRunner | None = None,
) -> DeploymentResult:
    runner = runner or CommandRunner()
    context = target_context(config, context)
    _prepare_ecs(config, context, runner)
    return DeploymentResult(context.sha, (), ())


def _prepare_s3(
    config: PipelineConfig, context: RuntimeContext, runner: CommandRunner
) -> tuple[_S3Operation, ...]:
    operations = []
    for deployment in _deployments(config, "s3", context.environment):
        if deployment.artifact_bucket is None:
            raise ConfigError(
                f"S3 deployment {deployment.name!r} has no artifact bucket"
            )
        bucket = resolve_reference(deployment.artifact_bucket, context)
        prefix = render_s3_prefix(deployment.artifact_prefix, context.template_values())
        artifact_uri = f"s3://{bucket}/{prefix}"
        result = _run_aws(runner, ["aws", "s3", "ls", f"{artifact_uri}/"], check=False)
        if result.returncode != 0 or not result.stdout.strip():
            raise ConfigError(
                f"No build artifact exists for {deployment.name!r} at {artifact_uri}"
            )
        targets = []
        for target in deployment.targets:
            target_bucket = _required_resolved_value(
                resolve_reference(target.bucket, context),
                f"S3 deployment {deployment.name!r} target bucket",
            )
            head_result = _run_aws(
                runner,
                ["aws", "s3api", "head-bucket", "--bucket", target_bucket],
                check=False,
                log_output=False,
            )
            if head_result.returncode != 0:
                raise ConfigError(
                    f"S3 target bucket {target_bucket!r} does not exist or is inaccessible"
                )
            targets.append(_S3Target(target_bucket, target.delete))

        invalidations = []
        for invalidation in deployment.invalidations:
            distribution = _required_resolved_value(
                resolve_reference(invalidation.distribution, context),
                f"S3 deployment {deployment.name!r} CloudFront distribution",
            )
            distribution_result = _run_aws(
                runner,
                [
                    "aws",
                    "cloudfront",
                    "get-distribution",
                    "--id",
                    distribution,
                    "--query",
                    "Distribution.Status",
                    "--output",
                    "text",
                ],
                check=False,
                log_output=False,
            )
            if distribution_result.returncode != 0:
                raise ConfigError(
                    f"CloudFront distribution {distribution!r} does not exist or is inaccessible"
                )
            invalidations.append(
                _CloudFrontInvalidation(distribution, invalidation.paths)
            )
        operations.append(
            _S3Operation(
                deployment.name,
                artifact_uri,
                tuple(targets),
                tuple(invalidations),
            )
        )
    return tuple(operations)


def _required_resolved_value(value: str, location: str) -> str:
    if not value.strip():
        raise ConfigError(f"{location} resolved empty")
    return value


def _prepare_ecs(
    config: PipelineConfig, context: RuntimeContext, runner: CommandRunner
) -> tuple[_EcsState, ...]:
    states = []
    for deployment in _deployments(config, "ecs", context.environment):
        if deployment.repository is None:
            raise ConfigError(f"ECS deployment {deployment.name!r} has no repository")
        repository = resolve_reference(deployment.repository, context)
        desired_image = f"{repository}:{context.sha}"
        _ensure_ecr_image(repository, context.sha, runner)
        for service_config in deployment.services:
            cluster = resolve_reference(service_config.cluster, context)
            service = resolve_reference(service_config.service, context)
            container = resolve_reference(service_config.container, context)
            service_document = _json_command(
                runner,
                [
                    "aws",
                    "ecs",
                    "describe-services",
                    "--cluster",
                    cluster,
                    "--services",
                    service,
                    "--output",
                    "json",
                ],
            )
            services = service_document.get("services", [])
            failures = service_document.get("failures", [])
            if failures or len(services) != 1:
                raise ConfigError(
                    f"Unable to resolve ECS service {cluster}/{service}: {failures!r}"
                )
            task_definition_arn = services[0].get("taskDefinition")
            if not isinstance(task_definition_arn, str) or not task_definition_arn:
                raise ConfigError(
                    f"ECS service {cluster}/{service} has no task definition"
                )
            task_document = _json_command(
                runner,
                [
                    "aws",
                    "ecs",
                    "describe-task-definition",
                    "--task-definition",
                    task_definition_arn,
                    "--output",
                    "json",
                ],
            )
            task_definition = task_document.get("taskDefinition")
            if not isinstance(task_definition, Mapping):
                raise ConfigError(
                    f"Task definition {task_definition_arn} returned no definition"
                )
            containers = [
                item
                for item in task_definition.get("containerDefinitions", [])
                if isinstance(item, Mapping) and item.get("name") == container
            ]
            if len(containers) != 1 or not isinstance(containers[0].get("image"), str):
                raise ConfigError(
                    f"Task definition {task_definition_arn} has no unique container {container!r}"
                )
            ssm_parameter = None
            if service_config.ssm_parameter:
                ssm_parameter = _required_resolved_value(
                    render(
                        service_config.ssm_parameter,
                        {
                            **context.template_values(),
                            "cluster": cluster,
                            "service": service,
                            "container": container,
                        },
                    ),
                    f"ECS service {cluster}/{service} SSM parameter",
                )
            states.append(
                _EcsState(
                    deployment=deployment,
                    service_config=service_config,
                    cluster=cluster,
                    service=service,
                    container=container,
                    desired_image=desired_image,
                    current_image=containers[0]["image"],
                    task_definition_arn=task_definition_arn,
                    task_definition=task_definition,
                    ssm_parameter=ssm_parameter,
                )
            )
    return tuple(states)


def _ensure_ecr_image(
    repository_uri: str, image_tag: str, runner: CommandRunner
) -> None:
    registry_id, repository_name = _parse_ecr_repository(repository_uri)
    command = [
        "aws",
        "ecr",
        "describe-images",
        "--repository-name",
        repository_name,
        "--image-ids",
        f"imageTag={image_tag}",
        "--output",
        "json",
    ]
    if registry_id:
        command.extend(["--registry-id", registry_id])
    result = _run_aws(runner, command, check=False, log_output=False)
    if result.returncode != 0:
        raise ConfigError(
            f"ECR image {repository_uri}:{image_tag} does not exist or is inaccessible"
        )
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ConfigError(
            f"ECR returned invalid JSON for {repository_uri}:{image_tag}: {error}"
        ) from error
    if not isinstance(document, Mapping) or not document.get("imageDetails"):
        raise ConfigError(f"ECR image {repository_uri}:{image_tag} does not exist")


def _parse_ecr_repository(repository_uri: str) -> tuple[str, str]:
    if "/" not in repository_uri:
        return "", repository_uri
    registry, repository_name = repository_uri.split("/", 1)
    if ".dkr.ecr." not in registry:
        return "", repository_uri
    registry_id = registry.split(".", 1)[0]
    return registry_id, repository_name


def _register_task_definition(state: _EcsState, runner: CommandRunner) -> str:
    task_definition = copy.deepcopy(dict(state.task_definition))
    for container in task_definition["containerDefinitions"]:
        if container.get("name") == state.container:
            container["image"] = state.desired_image
    registration = {
        key: value
        for key, value in task_definition.items()
        if key in REGISTER_TASK_DEFINITION_FIELDS
    }
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", encoding="utf-8", delete=False
        ) as temporary_file:
            json.dump(registration, temporary_file, separators=(",", ":"))
            temporary_path = Path(temporary_file.name)
        result = _json_command(
            runner,
            [
                "aws",
                "ecs",
                "register-task-definition",
                "--cli-input-json",
                f"file://{temporary_path}",
                "--output",
                "json",
            ],
        )
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
    arn = result.get("taskDefinition", {}).get("taskDefinitionArn")
    if not isinstance(arn, str) or not arn:
        raise ConfigError("Registering the ECS task definition returned no ARN")
    return arn


def _update_ssm(state: _EcsState, runner: CommandRunner) -> None:
    if not state.ssm_parameter:
        return
    _run_aws(
        runner,
        [
            "aws",
            "ssm",
            "put-parameter",
            "--name",
            state.ssm_parameter,
            "--value",
            state.desired_image,
            "--type",
            "String",
            "--overwrite",
        ],
    )


def _json_command(runner: CommandRunner, arguments: Sequence[str]) -> Mapping[str, Any]:
    result = _run_aws(runner, arguments, log_output=False)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ConfigError(
            f"Command returned invalid JSON: {' '.join(arguments)}: {error}"
        ) from error
    if not isinstance(value, Mapping):
        raise ConfigError(f"Command returned a non-object: {' '.join(arguments)}")
    return value


def _run_aws(
    runner: CommandRunner,
    arguments: Sequence[str],
    *,
    check: bool = True,
    log_output: bool = True,
):
    return runner.run(
        arguments,
        check=check,
        log_output=log_output,
        unset_environment=DEPLOYMENT_WORKFLOW_SECRETS,
    )


def _deployments(
    config: PipelineConfig, kind: str, environment: str
) -> tuple[DeploymentConfig, ...]:
    return tuple(
        deployment
        for deployment in config.deployments
        if deployment.kind == kind and environment in deployment.environments
    )
