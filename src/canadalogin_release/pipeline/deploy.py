from __future__ import annotations

import copy
import json
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..config import (
    ConfigError,
    DeploymentConfig,
    EcsServiceConfig,
    PipelineConfig,
    ValueReference,
)
from ..support.commands import CommandError, CommandRunner, log
from ..support.runtime import (
    RuntimeContext,
    render,
    render_s3_prefix,
    resolve_reference,
)

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
ECS_ROLLOUT_TIMEOUT_SECONDS = 600
ECS_ROLLOUT_POLL_INTERVAL_SECONDS = 15
ECS_MIGRATION_TIMEOUT_SECONDS = 900
ECS_MIGRATION_POLL_INTERVAL_SECONDS = 15
ECS_MIGRATION_CLEANUP_TIMEOUT_SECONDS = 120
ECS_MIGRATION_COMMAND_TIMEOUT_SECONDS = 60
MIGRATION_CONTAINER = "migrations"
MIGRATION_COMMAND = ("alembic", "upgrade", "head")


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
    desired_image_digest: str | None
    current_image: str
    task_definition_arn: str
    primary_deployment_id: str | None
    task_definition: Mapping[str, Any]
    ssm_parameter: str | None
    network_configuration: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class _MigrationState:
    ecs: _EcsState
    network_configuration: Mapping[str, Any]


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
            destination = f"s3://{target.bucket}"
            log(f"S3: syncing {operation.artifact_uri} to {destination}.")
            command = [
                "aws",
                "s3",
                "sync",
                operation.artifact_uri,
                destination,
            ]
            command.append("--only-show-errors")
            if target.delete:
                command.append("--delete")
            _run_aws(runner, command)
            changed.append(destination)
        for invalidation in operation.invalidations:
            paths = ", ".join(invalidation.paths)
            log(f"CloudFront: invalidating {invalidation.distribution} ({paths}).")
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


# This method validates that:
# - The configured build artifacts exist in S3.
# - Target S3 buckets and CloudFront distributions are accessible.
def preflight_s3(
    config: PipelineConfig,
    context: RuntimeContext,
    *,
    runner: CommandRunner | None = None,
) -> DeploymentResult:
    runner = runner or CommandRunner()
    context = target_context(config, context)
    operations = _prepare_s3(config, context, runner)
    target_count = sum(len(operation.targets) for operation in operations)
    invalidation_count = sum(
        len(operation.invalidations) for operation in operations
    )
    log(
        "S3 preflight complete: verified "
        f"{len(operations)} deployment(s), {target_count} target(s), "
        f"and {invalidation_count} CloudFront distribution(s)."
    )
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
        deployment_image = _desired_image_reference(state)
        image_matches_desired = _image_matches_desired(state)
        if image_matches_desired and not force_redeploy:
            log(
                f"{resource}: already runs {state.desired_image}; "
                "checking service stability."
            )
            _wait_for_service_stability(
                state,
                runner,
                expected_task_definition_arn=state.task_definition_arn,
            )
            _update_ssm(state, runner)
            log(f"{resource}: no deployment needed.")
            unchanged.append(resource)
            continue

        task_definition_arn = state.task_definition_arn
        force_same_image = image_matches_desired and force_redeploy
        if force_same_image and state.primary_deployment_id is None:
            raise ConfigError(
                f"ECS service {state.cluster}/{state.service} has no primary "
                "deployment identity for a forced redeploy"
            )
        if not image_matches_desired:
            log(f"{resource}: registering task definition for {deployment_image}.")
            task_definition_arn = _register_task_definition(state, runner)

        log(f"{resource}: updating service to {deployment_image}.")
        update_command = [
            "aws",
            "ecs",
            "update-service",
            "--cluster",
            state.cluster,
            "--service",
            state.service,
        ]
        if image_matches_desired:
            update_command.append("--force-new-deployment")
        else:
            update_command.extend(["--task-definition", task_definition_arn])
        update_command.extend(["--propagate-tags", "SERVICE"])
        _run_aws(runner, update_command)
        _wait_for_service_stability(
            state,
            runner,
            expected_task_definition_arn=task_definition_arn,
            previous_deployment_id=(
                state.primary_deployment_id if force_same_image else None
            ),
        )
        if not image_matches_desired:
            _verify_task_definition_image(state, task_definition_arn, runner)
        _update_ssm(state, runner)
        log(f"{resource}: deployment complete.")
        changed.append(resource)
    return DeploymentResult(context.sha, tuple(changed), tuple(unchanged))


def _wait_for_service_stability(
    state: _EcsState,
    runner: CommandRunner,
    *,
    expected_task_definition_arn: str,
    previous_deployment_id: str | None = None,
) -> None:
    deadline = time.monotonic() + ECS_ROLLOUT_TIMEOUT_SECONDS
    resource = f"ecs:{state.cluster}/{state.service}"
    command = [
        "aws",
        "ecs",
        "describe-services",
        "--cluster",
        state.cluster,
        "--services",
        state.service,
        "--output",
        "json",
    ]
    diagnostics = "no ECS service response"
    last_reported_diagnostics = None
    while True:
        result = _run_aws(runner, command, check=False, log_output=False)
        if result.returncode != 0:
            error = (result.stderr or result.stdout).strip()
            diagnostics = f"describe-services error={error or 'request failed'}"
        else:
            try:
                document = json.loads(result.stdout)
            except json.JSONDecodeError:
                document = None
                diagnostics = "describe-services returned invalid JSON"
            if isinstance(document, Mapping):
                diagnostics = _ecs_service_diagnostics(
                    document,
                    expected_task_definition_arn=expected_task_definition_arn,
                    previous_deployment_id=previous_deployment_id,
                )
                if _ecs_rollout_failed(
                    document,
                    expected_task_definition_arn=expected_task_definition_arn,
                    previous_deployment_id=previous_deployment_id,
                ):
                    raise ConfigError(
                        f"ECS service {state.cluster}/{state.service} reported a "
                        f"failed rollout: {diagnostics}"
                    )
                if _ecs_service_is_stable(
                    document,
                    expected_task_definition_arn=expected_task_definition_arn,
                    previous_deployment_id=previous_deployment_id,
                ):
                    log(f"{resource}: rollout complete.")
                    return
            elif document is not None:
                diagnostics = "describe-services returned a non-object"
        if diagnostics != last_reported_diagnostics:
            log(f"{resource}: waiting for rollout ({diagnostics}).")
            last_reported_diagnostics = diagnostics

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ConfigError(
                f"ECS service {state.cluster}/{state.service} did not become stable "
                f"within {ECS_ROLLOUT_TIMEOUT_SECONDS} seconds: {diagnostics}"
            )
        time.sleep(min(ECS_ROLLOUT_POLL_INTERVAL_SECONDS, remaining))


def _ecs_service_is_stable(
    document: Mapping[str, Any],
    *,
    expected_task_definition_arn: str | None = None,
    previous_deployment_id: str | None = None,
) -> bool:
    services = document.get("services", [])
    if not isinstance(services, list) or len(services) != 1:
        return False
    service = services[0]
    if not isinstance(service, Mapping):
        return False
    deployments = service.get("deployments", [])
    if not isinstance(deployments, list):
        return False

    matching_deployments = [
        deployment
        for deployment in deployments
        if isinstance(deployment, Mapping)
        and _ecs_deployment_matches_expected(
            deployment,
            expected_task_definition_arn=expected_task_definition_arn,
            previous_deployment_id=previous_deployment_id,
        )
    ]
    if len(matching_deployments) != 1:
        return False

    deployment = matching_deployments[0]
    desired_count = service.get("desiredCount")
    return (
        _ecs_deployment_is_ready(deployment, desired_count)
        and service.get("pendingCount", 0) == 0
    )


def _ecs_deployment_matches_expected(
    deployment: Mapping[str, Any],
    *,
    expected_task_definition_arn: str | None,
    previous_deployment_id: str | None,
) -> bool:
    return (
        deployment.get("status") == "PRIMARY"
        and (
            expected_task_definition_arn is None
            or deployment.get("taskDefinition") == expected_task_definition_arn
        )
        and (
            previous_deployment_id is None
            or deployment.get("id") != previous_deployment_id
        )
    )


def _ecs_deployment_is_ready(deployment: Mapping[str, Any], desired_count: Any) -> bool:
    return (
        deployment.get("desiredCount") == desired_count
        and deployment.get("runningCount") == desired_count
        and deployment.get("pendingCount", 0) == 0
        and deployment.get("rolloutState", "COMPLETED") == "COMPLETED"
    )


def _ecs_rollout_failed(
    document: Mapping[str, Any],
    *,
    expected_task_definition_arn: str | None = None,
    previous_deployment_id: str | None = None,
) -> bool:
    services = document.get("services", [])
    if not isinstance(services, list) or len(services) != 1:
        return bool(document.get("failures"))
    service = services[0]
    if not isinstance(service, Mapping):
        return False
    deployments = service.get("deployments", [])
    if not isinstance(deployments, list):
        return False
    return any(
        isinstance(deployment, Mapping)
        and deployment.get("rolloutState") == "FAILED"
        and (
            expected_task_definition_arn is None
            or deployment.get("taskDefinition") == expected_task_definition_arn
        )
        and (
            previous_deployment_id is None
            or deployment.get("id") != previous_deployment_id
        )
        for deployment in deployments
    )


def _ecs_service_diagnostics(
    document: Mapping[str, Any],
    *,
    expected_task_definition_arn: str | None = None,
    previous_deployment_id: str | None = None,
) -> str:
    services = document.get("services", [])
    if not isinstance(services, list) or len(services) != 1:
        return f"diagnostics_unavailable=unexpected services response: {services!r}"
    service = services[0]
    if not isinstance(service, Mapping):
        return "diagnostics_unavailable=service response was not an object"

    deployments = service.get("deployments", [])
    rollout_states = []
    deployment_entries = []
    if isinstance(deployments, list):
        for index, deployment in enumerate(deployments):
            if not isinstance(deployment, Mapping):
                continue
            deployment_entries.append((index, deployment))
            rollout_states.append(_ecs_deployment_summary(deployment))

    expected_indices = {
        index
        for index, deployment in deployment_entries
        if _ecs_deployment_matches_expected(
            deployment,
            expected_task_definition_arn=expected_task_definition_arn,
            previous_deployment_id=previous_deployment_id,
        )
    }
    expected_deployments = [
        deployment
        for index, deployment in deployment_entries
        if index in expected_indices
    ]
    other_deployments = [
        deployment
        for index, deployment in deployment_entries
        if index not in expected_indices
    ]
    if len(expected_deployments) == 1:
        expected_deployment = expected_deployments[0]
        expected_summary = _ecs_deployment_summary(expected_deployment)
        if _ecs_deployment_is_ready(expected_deployment, service.get("desiredCount")):
            if other_deployments:
                rollout_status = (
                    f"expected deployment is ready ({expected_summary}); "
                    "waiting on other deployment(s): "
                    + ", ".join(
                        _ecs_deployment_summary(deployment)
                        for deployment in other_deployments
                    )
                )
            else:
                rollout_status = (
                    f"expected deployment is ready ({expected_summary}); "
                    "waiting for ECS service stability"
                )
        else:
            rollout_status = (
                "expected deployment is still starting or reaching capacity: "
                + expected_summary
            )
    elif expected_deployments:
        rollout_status = "expected deployment identity is ambiguous: " + ", ".join(
            _ecs_deployment_summary(deployment) for deployment in expected_deployments
        )
    elif expected_task_definition_arn is not None:
        rollout_status = (
            "expected deployment not found: "
            f"task_definition={expected_task_definition_arn}"
        )
    else:
        rollout_status = "expected deployment not found"
    events = service.get("events", [])
    recent_events = []
    if isinstance(events, list):
        for event in events[:5]:
            if isinstance(event, Mapping) and event.get("message"):
                recent_events.append(" ".join(str(event["message"]).split()))
    return (
        f"{rollout_status}; rollout_states=[{'; '.join(rollout_states)}]; "
        f"counts=running:{service.get('runningCount')}, "
        f"desired:{service.get('desiredCount')}, pending:{service.get('pendingCount')}; "
        f"recent_events=[{' | '.join(recent_events)}]"
    )


def _ecs_deployment_summary(deployment: Mapping[str, Any]) -> str:
    state = (
        f"id={deployment.get('id')} "
        f"status={deployment.get('status')} "
        f"rollout_state={deployment.get('rolloutState')} "
        f"task_definition={deployment.get('taskDefinition')} "
        f"desired={deployment.get('desiredCount')} "
        f"running={deployment.get('runningCount')} "
        f"pending={deployment.get('pendingCount')} "
        f"failed_tasks={deployment.get('failedTasks')}"
    )
    reason = deployment.get("rolloutStateReason")
    if reason:
        state += f" reason={reason}"
    return state

# This method validates that:
# - The ECR image tagged with the planned deployment SHA exists.
# - ECS services, their task definitions, and configured containers are accessible.
# - Configured SSM parameter names resolve to non-empty values.
def preflight_ecs(
    config: PipelineConfig,
    context: RuntimeContext,
    *,
    runner: CommandRunner | None = None,
) -> DeploymentResult:
    runner = runner or CommandRunner()
    context = target_context(config, context)
    states = _prepare_ecs(config, context, runner)
    _prepare_migrations(states, context, runner)
    log(f"ECS preflight complete: verified {len(states)} service(s).")
    return DeploymentResult(context.sha, (), ())


def migrate_ecs(
    config: PipelineConfig,
    context: RuntimeContext,
    *,
    runner: CommandRunner | None = None,
) -> DeploymentResult:
    """Run one candidate-image migration task before either component deploys."""
    runner = runner or CommandRunner()
    context = target_context(config, context)
    migration_config = replace(
        config,
        deployments=tuple(
            deployment for deployment in config.deployments if deployment.migrations
        ),
    )
    states = _prepare_migrations(
        _prepare_ecs(migration_config, context, runner), context, runner
    )
    changed = []
    for state in states:
        _run_migration(state, runner)
        changed.append(f"ecs-migration:{state.ecs.cluster}/{state.ecs.deployment.name}")
    return DeploymentResult(context.sha, tuple(changed), ())


def _prepare_migrations(
    states: Sequence[_EcsState], context: RuntimeContext, runner: CommandRunner
) -> tuple[_MigrationState, ...]:
    migrations = []
    seen = set()
    for state in states:
        if not state.deployment.migrations or state.deployment.name in seen:
            continue
        seen.add(state.deployment.name)
        reference = resolve_reference(
            ValueReference("var", "RELEASE_ECS_MIGRATION_TASK_DEFINITION"), context
        )
        reference = _required_resolved_value(reference, "Migration task definition")
        document = _json_command(
            runner,
            [
                "aws",
                "ecs",
                "describe-task-definition",
                "--task-definition",
                reference,
                "--output",
                "json",
            ],
        )
        definition = document.get("taskDefinition")
        if not isinstance(definition, Mapping):
            raise ConfigError(
                f"Migration task definition {reference} returned no definition"
            )
        containers = definition.get("containerDefinitions", [])
        if (
            not isinstance(containers, list)
            or len(containers) != 1
            or not isinstance(containers[0], Mapping)
            or containers[0].get("name") != MIGRATION_CONTAINER
            or not containers[0].get("essential", True)
        ):
            raise ConfigError(
                "Migration task definition must have exactly one essential container "
                f"named {MIGRATION_CONTAINER!r}"
            )
        if (
            definition.get("networkMode") != "awsvpc"
            or "FARGATE" not in definition.get("requiresCompatibilities", [])
            or not definition.get("taskRoleArn")
            or not definition.get("executionRoleArn")
        ):
            raise ConfigError(
                "Migration task definition requires Fargate, awsvpc and task/execution roles"
            )
        network = state.network_configuration
        awsvpc = (
            network.get("awsvpcConfiguration") if isinstance(network, Mapping) else None
        )
        if (
            not isinstance(awsvpc, Mapping)
            or not awsvpc.get("subnets")
            or not awsvpc.get("securityGroups")
        ):
            raise ConfigError(
                "Migration source service requires awsvpc subnets and security groups"
            )
        if not state.desired_image_digest:
            raise ConfigError("Migration image requires a verified ECR digest")
        migrations.append(
            _MigrationState(
                replace(
                    state, task_definition=definition, container=MIGRATION_CONTAINER
                ),
                network,
            )
        )
    return tuple(migrations)


def _run_migration(state: _MigrationState, runner: CommandRunner) -> None:
    ecs = state.ecs
    revision = _register_task_definition(
        ecs,
        runner,
        command=MIGRATION_COMMAND,
        timeout=ECS_MIGRATION_COMMAND_TIMEOUT_SECONDS,
    )
    task_arn = None
    completed = False
    try:
        log(
            f"ecs:{ecs.cluster}: starting migrations with {_desired_image_reference(ecs)}."
        )
        document = _json_command(
            runner,
            [
                "aws",
                "ecs",
                "run-task",
                "--cluster",
                ecs.cluster,
                "--task-definition",
                revision,
                "--launch-type",
                "FARGATE",
                "--count",
                "1",
                "--client-token",
                str(uuid.uuid4()),
                "--network-configuration",
                json.dumps(state.network_configuration),
                "--output",
                "json",
            ],
            timeout=ECS_MIGRATION_COMMAND_TIMEOUT_SECONDS,
        )
        tasks = document.get("tasks", [])
        if (
            isinstance(tasks, list)
            and len(tasks) == 1
            and isinstance(tasks[0], Mapping)
        ):
            task_arn = tasks[0].get("taskArn")
        if document.get("failures") or not isinstance(task_arn, str) or not task_arn:
            raise ConfigError(
                f"Migration task could not start: {document.get('failures', [])!r}"
            )
        log(f"Migration task: {task_arn}")
        _wait_for_migration(ecs.cluster, task_arn, runner)
        completed = True
        log(f"Migration task {task_arn}: completed successfully.")
    finally:
        if task_arn and not completed:
            stopped = _migration_cleanup(
                runner,
                [
                    "aws",
                    "ecs",
                    "stop-task",
                    "--cluster",
                    ecs.cluster,
                    "--task",
                    task_arn,
                    "--reason",
                    "Release pipeline migration failed or timed out",
                ],
            )
            if stopped:
                try:
                    _wait_for_migration(ecs.cluster, task_arn, runner, stopping=True)
                except (CommandError, ConfigError, OSError):
                    log(
                        f"::warning::Could not confirm migration task {task_arn} stopped; unsafe to retry until inspected."
                    )
        elif not task_arn:
            log(
                f"::warning::No migration task ARN was received for {revision}; inspect ECS for an ambiguous launch before retrying."
            )
        _migration_cleanup(
            runner,
            ["aws", "ecs", "deregister-task-definition", "--task-definition", revision],
        )


def _wait_for_migration(
    cluster: str, task_arn: str, runner: CommandRunner, *, stopping: bool = False
) -> None:
    timeout = (
        ECS_MIGRATION_CLEANUP_TIMEOUT_SECONDS
        if stopping
        else ECS_MIGRATION_TIMEOUT_SECONDS
    )
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ConfigError(
                f"Migration task {task_arn} timed out after {timeout} seconds"
            )
        document = _json_command(
            runner,
            [
                "aws",
                "ecs",
                "describe-tasks",
                "--cluster",
                cluster,
                "--tasks",
                task_arn,
                "--output",
                "json",
            ],
            timeout=min(ECS_MIGRATION_COMMAND_TIMEOUT_SECONDS, remaining),
        )
        tasks = document.get("tasks", [])
        failures = document.get("failures", [])
        invisible = tasks == [] and (
            failures == []
            or (
                isinstance(failures, list)
                and all(
                    isinstance(failure, Mapping) and failure.get("reason") == "MISSING"
                    for failure in failures
                )
            )
        )
        if invisible:
            # RunTask and DescribeTasks are eventually consistent. Retry only
            # observation, never execution of potentially partially applied DDL.
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(ECS_MIGRATION_POLL_INTERVAL_SECONDS, remaining))
            continue
        if (
            document.get("failures")
            or not isinstance(tasks, list)
            or len(tasks) != 1
            or not isinstance(tasks[0], Mapping)
            or tasks[0].get("taskArn") != task_arn
        ):
            raise ConfigError(
                f"Unable to describe migration task {task_arn}: {document.get('failures', [])!r}"
            )
        task = tasks[0]
        if task.get("lastStatus") == "STOPPED":
            if stopping:
                return
            containers = task.get("containers", [])
            migration_containers = (
                [
                    container
                    for container in containers
                    if isinstance(container, Mapping)
                    and container.get("name") == MIGRATION_CONTAINER
                ]
                if isinstance(containers, list)
                else []
            )
            if (
                len(migration_containers) != 1
                or type(migration_containers[0].get("exitCode")) is not int
                or migration_containers[0]["exitCode"] != 0
                or task.get("stopCode") != "EssentialContainerExited"
            ):
                exit_code = (
                    migration_containers[0].get("exitCode")
                    if len(migration_containers) == 1
                    else None
                )
                reason = (
                    migration_containers[0].get("reason")
                    if len(migration_containers) == 1
                    else None
                )
                raise ConfigError(
                    f"Migration task {task_arn} failed: exit_code={exit_code}, "
                    f"stop_code={task.get('stopCode')}, reason={reason or task.get('stoppedReason')}"
                )
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ConfigError(
                f"Migration task {task_arn} timed out after {timeout} seconds"
            )
        time.sleep(min(ECS_MIGRATION_POLL_INTERVAL_SECONDS, remaining))


def _migration_cleanup(runner: CommandRunner, command: Sequence[str]) -> bool:
    # Cleanup errors must not mask the original migration failure.
    try:
        result = _run_aws(
            runner, command, check=False, timeout=ECS_MIGRATION_COMMAND_TIMEOUT_SECONDS
        )
        if result.returncode != 0:
            log(
                f"::warning::Migration cleanup failed: {' '.join(command)}; inspect ECS before retrying."
            )
            return False
        return True
    except (CommandError, OSError):
        log(
            f"::warning::Migration cleanup failed: {' '.join(command)}; inspect ECS before retrying."
        )
        return False


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
        desired_image_digest = _ensure_ecr_image(repository, context.sha, runner)
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
            service_document_value = services[0]
            task_definition_arn = service_document_value.get("taskDefinition")
            if not isinstance(task_definition_arn, str) or not task_definition_arn:
                raise ConfigError(
                    f"ECS service {cluster}/{service} has no task definition"
                )
            deployments = service_document_value.get("deployments", [])
            primary_deployments = [
                deployment
                for deployment in deployments
                if isinstance(deployment, Mapping)
                and deployment.get("status") == "PRIMARY"
            ]
            primary_deployment_id = None
            if len(primary_deployments) == 1:
                candidate_id = primary_deployments[0].get("id")
                if isinstance(candidate_id, str) and candidate_id:
                    primary_deployment_id = candidate_id
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
                    desired_image_digest=desired_image_digest,
                    current_image=containers[0]["image"],
                    task_definition_arn=task_definition_arn,
                    primary_deployment_id=primary_deployment_id,
                    task_definition=task_definition,
                    ssm_parameter=ssm_parameter,
                    network_configuration=service_document_value.get(
                        "networkConfiguration"
                    ),
                )
            )
    return tuple(states)


def _ensure_ecr_image(
    repository_uri: str, image_tag: str, runner: CommandRunner
) -> str | None:
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
    image_details = (
        document.get("imageDetails") if isinstance(document, Mapping) else None
    )
    if not isinstance(image_details, list) or len(image_details) != 1:
        raise ConfigError(f"ECR image {repository_uri}:{image_tag} does not exist")
    image_digest = image_details[0].get("imageDigest")
    if registry_id and (not isinstance(image_digest, str) or not image_digest):
        raise ConfigError(
            f"ECR image {repository_uri}:{image_tag} returned no image digest"
        )
    return image_digest if isinstance(image_digest, str) else None


def _parse_ecr_repository(repository_uri: str) -> tuple[str, str]:
    if "/" not in repository_uri:
        return "", repository_uri
    registry, repository_name = repository_uri.split("/", 1)
    if ".dkr.ecr." not in registry:
        return "", repository_uri
    registry_id = registry.split(".", 1)[0]
    return registry_id, repository_name


def _register_task_definition(
    state: _EcsState,
    runner: CommandRunner,
    *,
    command: Sequence[str] | None = None,
    timeout: float | None = None,
) -> str:
    task_definition = copy.deepcopy(dict(state.task_definition))
    desired_image = _desired_image_reference(state)
    for container in task_definition["containerDefinitions"]:
        if container.get("name") == state.container:
            container["image"] = desired_image
            if command is not None:
                container["command"] = list(command)
                container.pop("healthCheck", None)
                container.pop("restartPolicy", None)
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
            timeout=timeout,
        )
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
    arn = result.get("taskDefinition", {}).get("taskDefinitionArn")
    if not isinstance(arn, str) or not arn:
        raise ConfigError("Registering the ECS task definition returned no ARN")
    return arn


def _verify_task_definition_image(
    state: _EcsState, task_definition_arn: str, runner: CommandRunner
) -> None:
    document = _json_command(
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
    task_definition = document.get("taskDefinition")
    if not isinstance(task_definition, Mapping):
        raise ConfigError(
            f"Task definition {task_definition_arn} returned no definition after rollout"
        )
    containers = [
        item
        for item in task_definition.get("containerDefinitions", [])
        if isinstance(item, Mapping) and item.get("name") == state.container
    ]
    expected_image = _desired_image_reference(state)
    if len(containers) != 1 or containers[0].get("image") != expected_image:
        actual_image = containers[0].get("image") if len(containers) == 1 else None
        raise ConfigError(
            f"Task definition {task_definition_arn} deployed image {actual_image!r}; "
            f"expected {expected_image!r}"
        )


def _desired_image_reference(state: _EcsState) -> str:
    if state.desired_image_digest:
        repository, _ = state.desired_image.rsplit(":", 1)
        return f"{repository}@{state.desired_image_digest}"
    return state.desired_image


def _image_matches_desired(state: _EcsState) -> bool:
    return state.current_image in {
        state.desired_image,
        _desired_image_reference(state),
    }


def _update_ssm(state: _EcsState, runner: CommandRunner) -> None:
    if not state.ssm_parameter:
        return
    resource = f"ecs:{state.cluster}/{state.service}"
    log(f"{resource}: updating SSM parameter {state.ssm_parameter}.")
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


def _json_command(
    runner: CommandRunner, arguments: Sequence[str], *, timeout: float | None = None
) -> Mapping[str, Any]:
    result = _run_aws(runner, arguments, log_output=False, timeout=timeout)
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
    log_output: bool = False,
    timeout: float | None = None,
):
    return runner.run(
        arguments,
        check=check,
        log_output=log_output,
        **({"timeout": timeout} if timeout is not None else {}),
    )


def _deployments(
    config: PipelineConfig, kind: str, environment: str
) -> tuple[DeploymentConfig, ...]:
    return tuple(
        deployment
        for deployment in config.deployments
        if deployment.kind == kind and environment in deployment.environments
    )
