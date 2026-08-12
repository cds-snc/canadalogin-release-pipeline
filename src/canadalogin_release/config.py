from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when a release pipeline configuration is invalid."""


INFO_NOTIFICATION_WORKFLOW_SECRETS = frozenset(
    {
        "GC_SIGNIN_OPS_SLACK_INFO_WEBHOOK",
        "GC_SIGNIN_WEBSITE_OPS_SLACK_INFO_WEBHOOK",
    }
)
ALERT_NOTIFICATION_WORKFLOW_SECRETS = frozenset(
    {
        "CL_DEV_SLACK_ALERT_WEBHOOK",
        "GC_SIGNIN_OPS_SLACK_ALERT_WEBHOOK",
        "GC_SIGNIN_WEBSITE_OPS_SLACK_ALERT_WEBHOOK",
    }
)
NOTIFICATION_WORKFLOW_SECRETS = frozenset(
    {*INFO_NOTIFICATION_WORKFLOW_SECRETS, *ALERT_NOTIFICATION_WORKFLOW_SECRETS}
)
BUILD_WORKFLOW_SECRETS = frozenset(
    {
        "FRONTEND_APP_BUILD_ARTIFACTS_S3_BUCKET",
        "FRONTEND_URL",
        "VITE_API_BASE_URL",
        "VITE_BACKEND_API_URL",
        "VITE_GOOGLE_ANALYTICS_ID",
        *(f"BUILD_SECRET_{index}" for index in range(1, 9)),
    }
)
DEPLOYMENT_WORKFLOW_SECRETS = frozenset(
    {
        "CLOUDFRONT_DISTRIBUTION_ID",
        "FRONTEND_APP_BUILD_ARTIFACTS_S3_BUCKET",
        "FRONTEND_APP_CLOUDFRONT_DISTRIBUTION_ID",
        "FRONTEND_APP_S3_BUCKET",
        *(f"DEPLOY_SECRET_{index}" for index in range(1, 9)),
    }
)
SUPPORTED_WORKFLOW_SECRETS = frozenset(
    {
        *NOTIFICATION_WORKFLOW_SECRETS,
        *BUILD_WORKFLOW_SECRETS,
        *DEPLOYMENT_WORKFLOW_SECRETS,
        *(f"HOOK_SECRET_{index}" for index in range(1, 5)),
    }
)


@dataclass(frozen=True)
class ValueReference:
    source: str
    value: str
    default: str | None = None

    @classmethod
    def parse(
        cls,
        raw: object,
        location: str,
        allowed_secrets: frozenset[str] = SUPPORTED_WORKFLOW_SECRETS,
    ) -> ValueReference:
        if isinstance(raw, str):
            return cls("value", raw)
        if not isinstance(raw, Mapping):
            raise ConfigError(f"{location} must be a string or reference table")

        keys = set(raw)
        source_keys = keys & {"value", "var", "secret"}
        if len(source_keys) != 1 or keys - {"value", "var", "secret", "default"}:
            raise ConfigError(
                f"{location} must contain exactly one of: value, var, secret"
            )
        source = next(iter(source_keys))
        value = raw[source]
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{location}.{source} must be a non-empty string")
        if source == "secret" and value not in allowed_secrets:
            raise ConfigError(
                f"{location}.secret references {value!r}, which the reusable "
                "workflows do not expose"
            )
        default = raw.get("default")
        if default is not None and not isinstance(default, str):
            raise ConfigError(f"{location}.default must be a string")
        return cls(source, value, default)

    def resolve(
        self,
        variables: Mapping[str, str],
        environment: Mapping[str, str] | None = None,
    ) -> str:
        environment = environment or os.environ
        if self.source == "value":
            return self.value
        if self.source == "var":
            value = variables.get(self.value, self.default)
            if value is None:
                raise ConfigError(f"GitHub variable {self.value!r} is not set")
            return value
        value = environment.get(self.value, self.default)
        if value is None:
            raise ConfigError(f"GitHub secret {self.value!r} is not available")
        if not value:
            raise ConfigError(f"GitHub secret {self.value!r} is empty")
        return value


@dataclass(frozen=True)
class EnvironmentConfig:
    development: str
    deploy: tuple[str, ...]
    versioned: tuple[str, ...]
    version_directory: Path = Path(".deployed_versions")


@dataclass(frozen=True)
class ReleaseConfig:
    enabled: bool = True
    tag_prefix: str = "v"


@dataclass(frozen=True)
class NotificationConfig:
    info_webhook: ValueReference | None = None
    alert_webhooks: tuple[ValueReference, ...] = ()
    notify_development_failures: bool = True


@dataclass(frozen=True)
class S3ArtifactConfig:
    source: Path
    bucket: ValueReference
    prefix: str = "{sha}"
    delete: bool = False
    skip_if_exists_non_development: bool = False


@dataclass(frozen=True)
class DockerBuildConfig:
    context: Path
    dockerfile: Path
    repository: ValueReference
    tags: tuple[str, ...] = ("sha",)
    build_args: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SbomConfig:
    name: str
    dockerfile: Path


@dataclass(frozen=True)
class BuildConfig:
    name: str
    kind: str
    environments: tuple[str, ...]
    aws_role: str | None = None
    dns_audit: bool = False
    gates_deployment: bool = True
    shared_artifact: bool = False
    node_version: str | None = None
    source_environment: str | None = None
    working_directory: Path = Path(".")
    commands: tuple[tuple[str, ...], ...] = ()
    environment: Mapping[str, ValueReference] = field(default_factory=dict)
    s3_artifact: S3ArtifactConfig | None = None
    docker: DockerBuildConfig | None = None
    sbom: SbomConfig | None = None


@dataclass(frozen=True)
class S3TargetConfig:
    bucket: ValueReference
    delete: bool = False


@dataclass(frozen=True)
class CloudFrontInvalidationConfig:
    distribution: ValueReference
    paths: tuple[str, ...]


@dataclass(frozen=True)
class EcsServiceConfig:
    cluster: ValueReference
    service: ValueReference
    container: ValueReference
    ssm_parameter: str | None = None


@dataclass(frozen=True)
class DeploymentConfig:
    name: str
    kind: str
    environments: tuple[str, ...]
    aws_role: str
    artifact_bucket: ValueReference | None = None
    artifact_prefix: str = "{sha}"
    targets: tuple[S3TargetConfig, ...] = ()
    invalidations: tuple[CloudFrontInvalidationConfig, ...] = ()
    repository: ValueReference | None = None
    services: tuple[EcsServiceConfig, ...] = ()


@dataclass(frozen=True)
class HookConfig:
    before_deploy: tuple[tuple[str, ...], ...] = ()
    health_check: tuple[tuple[str, ...], ...] = ()
    after_deploy: tuple[tuple[str, ...], ...] = ()
    on_failure: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class PipelineConfig:
    application: str
    aws_region: str
    environments: EnvironmentConfig
    release: ReleaseConfig
    notifications: NotificationConfig
    builds: tuple[BuildConfig, ...]
    deployments: tuple[DeploymentConfig, ...]
    hooks: HookConfig
    repository_dispatch: Mapping[str, tuple[str, ...]]

    @classmethod
    def load(cls, path: str | Path) -> PipelineConfig:
        config_path = Path(path)
        try:
            with config_path.open("rb") as config_file:
                raw = tomllib.load(config_file)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ConfigError(f"Unable to load {config_path}: {error}") from error
        return _parse_pipeline(raw)

    def deployment_roles(self, environment: str) -> dict[str, str]:
        roles: dict[str, str] = {}
        for deployment in self.deployments:
            if environment not in deployment.environments:
                continue
            existing = roles.setdefault(deployment.kind, deployment.aws_role)
            if existing != deployment.aws_role:
                raise ConfigError(
                    f"Deployments of kind {deployment.kind!r} in {environment!r} "
                    "must use one AWS role"
                )
        return roles


def _parse_pipeline(raw: Mapping[str, Any]) -> PipelineConfig:
    _reject_unknown(
        raw,
        {
            "schema_version",
            "application",
            "aws_region",
            "environments",
            "release",
            "notifications",
            "builds",
            "deployments",
            "hooks",
            "events",
        },
        "configuration",
    )
    if raw.get("schema_version") != 1:
        raise ConfigError("schema_version must be 1")
    application = _required_string(raw, "application", "application")
    aws_region = _optional_string(raw, "aws_region", "ca-central-1", "aws_region")

    environments_raw = _required_mapping(raw, "environments", "environments")
    _reject_unknown(
        environments_raw,
        {"development", "deploy", "versioned", "version_directory"},
        "environments",
    )
    development = _required_string(
        environments_raw, "development", "environments.development"
    )
    deploy = _string_tuple(environments_raw.get("deploy"), "environments.deploy")
    versioned = _string_tuple(
        environments_raw.get("versioned", ()),
        "environments.versioned",
        allow_empty=True,
    )
    if development not in deploy:
        raise ConfigError(
            "environments.development must be listed in environments.deploy"
        )
    if development in versioned:
        raise ConfigError("the development environment cannot be versioned")
    _ensure_unique(deploy, "environments.deploy")
    _ensure_unique(versioned, "environments.versioned")
    environment_config = EnvironmentConfig(
        development=development,
        deploy=deploy,
        versioned=versioned,
        version_directory=Path(
            _optional_string(
                environments_raw,
                "version_directory",
                ".deployed_versions",
                "environments.version_directory",
            )
        ),
    )

    release_raw = _optional_mapping(raw, "release", "release")
    _reject_unknown(release_raw, {"enabled", "tag_prefix"}, "release")
    release = ReleaseConfig(
        enabled=_optional_bool(release_raw, "enabled", True, "release.enabled"),
        tag_prefix=_optional_string(
            release_raw, "tag_prefix", "v", "release.tag_prefix"
        ),
    )

    notifications_raw = _optional_mapping(raw, "notifications", "notifications")
    _reject_unknown(
        notifications_raw,
        {"info_webhook", "alert_webhooks", "notify_development_failures"},
        "notifications",
    )
    info_raw = notifications_raw.get("info_webhook")
    notifications = NotificationConfig(
        info_webhook=(
            ValueReference.parse(
                info_raw,
                "notifications.info_webhook",
                INFO_NOTIFICATION_WORKFLOW_SECRETS,
            )
            if info_raw is not None
            else None
        ),
        alert_webhooks=tuple(
            ValueReference.parse(
                value,
                f"notifications.alert_webhooks[{index}]",
                ALERT_NOTIFICATION_WORKFLOW_SECRETS,
            )
            for index, value in enumerate(
                _sequence(
                    notifications_raw.get("alert_webhooks", ()),
                    "notifications.alert_webhooks",
                )
            )
        ),
        notify_development_failures=_optional_bool(
            notifications_raw,
            "notify_development_failures",
            True,
            "notifications.notify_development_failures",
        ),
    )

    builds = tuple(
        _parse_build(value, index)
        for index, value in enumerate(_sequence(raw.get("builds", ()), "builds"))
    )
    deployments = tuple(
        _parse_deployment(value, index, deploy)
        for index, value in enumerate(
            _sequence(raw.get("deployments", ()), "deployments")
        )
    )
    _ensure_unique((build.name for build in builds), "build names")
    _ensure_unique((deployment.name for deployment in deployments), "deployment names")

    hooks_raw = _optional_mapping(raw, "hooks", "hooks")
    _reject_unknown(
        hooks_raw,
        {"before_deploy", "health_check", "after_deploy", "on_failure"},
        "hooks",
    )
    hooks = HookConfig(
        before_deploy=_commands(
            hooks_raw.get("before_deploy", ()), "hooks.before_deploy"
        ),
        health_check=_commands(hooks_raw.get("health_check", ()), "hooks.health_check"),
        after_deploy=_commands(hooks_raw.get("after_deploy", ()), "hooks.after_deploy"),
        on_failure=_commands(hooks_raw.get("on_failure", ()), "hooks.on_failure"),
    )

    events_raw = _optional_mapping(raw, "events", "events")
    _reject_unknown(events_raw, {"repository_dispatch"}, "events")
    dispatch_raw = _optional_mapping(
        events_raw, "repository_dispatch", "events.repository_dispatch"
    )
    repository_dispatch = {
        event: _string_tuple(
            targets, f"events.repository_dispatch.{event}", allow_empty=True
        )
        for event, targets in dispatch_raw.items()
    }

    config = PipelineConfig(
        application=application,
        aws_region=aws_region,
        environments=environment_config,
        release=release,
        notifications=notifications,
        builds=builds,
        deployments=deployments,
        hooks=hooks,
        repository_dispatch=repository_dispatch,
    )
    for environment in deploy:
        config.deployment_roles(environment)
    return config


def _parse_build(raw: object, index: int) -> BuildConfig:
    location = f"builds[{index}]"
    value = _strict_mapping(
        raw,
        location,
        {
            "name",
            "kind",
            "environments",
            "aws_role",
            "dns_audit",
            "gates_deployment",
            "shared_artifact",
            "node_version",
            "source_environment",
            "command",
            "s3_artifact",
            "docker",
            "sbom",
        },
    )
    name = _required_string(value, "name", f"{location}.name")
    kind = _required_string(value, "kind", f"{location}.kind")
    if kind not in {"command", "docker"}:
        raise ConfigError(f"{location}.kind must be 'command' or 'docker'")
    environments = _string_tuple(value.get("environments"), f"{location}.environments")

    command_raw = _optional_mapping(value, "command", f"{location}.command")
    _reject_unknown(
        command_raw,
        {"working_directory", "steps", "environment"},
        f"{location}.command",
    )
    commands = _commands(command_raw.get("steps", ()), f"{location}.command.steps")
    command_environment_raw = _optional_mapping(
        command_raw, "environment", f"{location}.command.environment"
    )
    command_environment = {
        key: ValueReference.parse(
            item,
            f"{location}.command.environment.{key}",
            BUILD_WORKFLOW_SECRETS,
        )
        for key, item in command_environment_raw.items()
    }
    if kind == "command" and not commands:
        raise ConfigError(f"{location}.command.steps must not be empty")

    artifact_raw = value.get("s3_artifact")
    artifact = None
    if artifact_raw is not None:
        artifact_value = _strict_mapping(
            artifact_raw,
            f"{location}.s3_artifact",
            {
                "source",
                "bucket",
                "prefix",
                "delete",
                "skip_if_exists_non_development",
            },
        )
        artifact = S3ArtifactConfig(
            source=Path(
                _required_string(
                    artifact_value, "source", f"{location}.s3_artifact.source"
                )
            ),
            bucket=ValueReference.parse(
                artifact_value.get("bucket"),
                f"{location}.s3_artifact.bucket",
                BUILD_WORKFLOW_SECRETS,
            ),
            prefix=_optional_string(
                artifact_value,
                "prefix",
                "{sha}",
                f"{location}.s3_artifact.prefix",
            ),
            delete=_optional_bool(
                artifact_value, "delete", False, f"{location}.s3_artifact.delete"
            ),
            skip_if_exists_non_development=_optional_bool(
                artifact_value,
                "skip_if_exists_non_development",
                False,
                f"{location}.s3_artifact.skip_if_exists_non_development",
            ),
        )

    docker_raw = value.get("docker")
    docker = None
    if docker_raw is not None:
        docker_value = _strict_mapping(
            docker_raw,
            f"{location}.docker",
            {"context", "dockerfile", "repository", "tags", "build_args"},
        )
        tags = _string_tuple(
            docker_value.get("tags", ("sha",)), f"{location}.docker.tags"
        )
        unsupported_tags = set(tags) - {"sha", "latest", "release"}
        if unsupported_tags:
            raise ConfigError(
                f"{location}.docker.tags contains unsupported values: "
                f"{', '.join(sorted(unsupported_tags))}"
            )
        build_args_raw = _optional_mapping(
            docker_value, "build_args", f"{location}.docker.build_args"
        )
        docker = DockerBuildConfig(
            context=Path(
                _required_string(docker_value, "context", f"{location}.docker.context")
            ),
            dockerfile=Path(
                _required_string(
                    docker_value, "dockerfile", f"{location}.docker.dockerfile"
                )
            ),
            repository=ValueReference.parse(
                docker_value.get("repository"),
                f"{location}.docker.repository",
                BUILD_WORKFLOW_SECRETS,
            ),
            tags=tags,
            build_args={
                key: _string(item, f"{location}.docker.build_args.{key}")
                for key, item in build_args_raw.items()
            },
        )
    if kind == "docker" and docker is None:
        raise ConfigError(f"{location}.docker is required for docker builds")
    if kind == "command" and docker is not None:
        raise ConfigError(f"{location}.docker is only valid for docker builds")

    sbom_raw = value.get("sbom")
    sbom = None
    if sbom_raw is not None:
        sbom_value = _strict_mapping(
            sbom_raw, f"{location}.sbom", {"name", "dockerfile"}
        )
        sbom = SbomConfig(
            name=_required_string(sbom_value, "name", f"{location}.sbom.name"),
            dockerfile=Path(
                _required_string(
                    sbom_value, "dockerfile", f"{location}.sbom.dockerfile"
                )
            ),
        )
        if kind != "docker":
            raise ConfigError(f"{location}.sbom is only valid for docker builds")

    return BuildConfig(
        name=name,
        kind=kind,
        environments=environments,
        aws_role=_optional_nullable_string(value, "aws_role", f"{location}.aws_role"),
        dns_audit=_optional_bool(value, "dns_audit", False, f"{location}.dns_audit"),
        gates_deployment=_optional_bool(
            value, "gates_deployment", True, f"{location}.gates_deployment"
        ),
        shared_artifact=_optional_bool(
            value, "shared_artifact", False, f"{location}.shared_artifact"
        ),
        node_version=_optional_nullable_string(
            value, "node_version", f"{location}.node_version"
        ),
        source_environment=_optional_nullable_string(
            value, "source_environment", f"{location}.source_environment"
        ),
        working_directory=Path(
            _optional_string(
                command_raw,
                "working_directory",
                ".",
                f"{location}.command.working_directory",
            )
        ),
        commands=commands,
        environment=command_environment,
        s3_artifact=artifact,
        docker=docker,
        sbom=sbom,
    )


def _parse_deployment(
    raw: object, index: int, default_environments: tuple[str, ...]
) -> DeploymentConfig:
    location = f"deployments[{index}]"
    value = _strict_mapping(
        raw,
        location,
        {
            "name",
            "kind",
            "environments",
            "aws_role",
            "artifact_bucket",
            "artifact_prefix",
            "targets",
            "invalidations",
            "repository",
            "services",
        },
    )
    name = _required_string(value, "name", f"{location}.name")
    kind = _required_string(value, "kind", f"{location}.kind")
    if kind not in {"s3", "ecs"}:
        raise ConfigError(f"{location}.kind must be 's3' or 'ecs'")
    environments = _string_tuple(
        value.get("environments", default_environments), f"{location}.environments"
    )
    unknown_environments = set(environments) - set(default_environments)
    if unknown_environments:
        raise ConfigError(
            f"{location}.environments contains undeployable environments: "
            f"{', '.join(sorted(unknown_environments))}"
        )

    targets = tuple(
        S3TargetConfig(
            bucket=ValueReference.parse(
                _strict_mapping(
                    target,
                    f"{location}.targets[{target_index}]",
                    {"bucket", "delete"},
                ).get("bucket"),
                f"{location}.targets[{target_index}].bucket",
                DEPLOYMENT_WORKFLOW_SECRETS,
            ),
            delete=_optional_bool(
                _strict_mapping(
                    target,
                    f"{location}.targets[{target_index}]",
                    {"bucket", "delete"},
                ),
                "delete",
                False,
                f"{location}.targets[{target_index}].delete",
            ),
        )
        for target_index, target in enumerate(
            _sequence(value.get("targets", ()), f"{location}.targets")
        )
    )
    invalidations = tuple(
        CloudFrontInvalidationConfig(
            distribution=ValueReference.parse(
                _mapping(
                    invalidation, f"{location}.invalidations[{invalidation_index}]"
                ).get("distribution"),
                f"{location}.invalidations[{invalidation_index}].distribution",
                DEPLOYMENT_WORKFLOW_SECRETS,
            ),
            paths=_string_tuple(
                _strict_mapping(
                    invalidation,
                    f"{location}.invalidations[{invalidation_index}]",
                    {"distribution", "paths"},
                ).get("paths"),
                f"{location}.invalidations[{invalidation_index}].paths",
            ),
        )
        for invalidation_index, invalidation in enumerate(
            _sequence(value.get("invalidations", ()), f"{location}.invalidations")
        )
    )
    services = tuple(
        EcsServiceConfig(
            cluster=ValueReference.parse(
                _strict_mapping(
                    service,
                    f"{location}.services[{service_index}]",
                    {"cluster", "service", "container", "ssm_parameter"},
                ).get("cluster"),
                f"{location}.services[{service_index}].cluster",
                DEPLOYMENT_WORKFLOW_SECRETS,
            ),
            service=ValueReference.parse(
                _strict_mapping(
                    service,
                    f"{location}.services[{service_index}]",
                    {"cluster", "service", "container", "ssm_parameter"},
                ).get("service"),
                f"{location}.services[{service_index}].service",
                DEPLOYMENT_WORKFLOW_SECRETS,
            ),
            container=ValueReference.parse(
                _strict_mapping(
                    service,
                    f"{location}.services[{service_index}]",
                    {"cluster", "service", "container", "ssm_parameter"},
                ).get("container"),
                f"{location}.services[{service_index}].container",
                DEPLOYMENT_WORKFLOW_SECRETS,
            ),
            ssm_parameter=_optional_nullable_string(
                _strict_mapping(
                    service,
                    f"{location}.services[{service_index}]",
                    {"cluster", "service", "container", "ssm_parameter"},
                ),
                "ssm_parameter",
                f"{location}.services[{service_index}].ssm_parameter",
            ),
        )
        for service_index, service in enumerate(
            _sequence(value.get("services", ()), f"{location}.services")
        )
    )

    artifact_bucket = (
        ValueReference.parse(
            value.get("artifact_bucket"),
            f"{location}.artifact_bucket",
            DEPLOYMENT_WORKFLOW_SECRETS,
        )
        if value.get("artifact_bucket") is not None
        else None
    )
    repository = (
        ValueReference.parse(
            value.get("repository"),
            f"{location}.repository",
            DEPLOYMENT_WORKFLOW_SECRETS,
        )
        if value.get("repository") is not None
        else None
    )
    if kind == "s3" and (artifact_bucket is None or not targets):
        raise ConfigError(
            f"{location} requires artifact_bucket and at least one target"
        )
    if kind == "ecs" and (repository is None or not services):
        raise ConfigError(f"{location} requires repository and at least one service")

    return DeploymentConfig(
        name=name,
        kind=kind,
        environments=environments,
        aws_role=_required_string(value, "aws_role", f"{location}.aws_role"),
        artifact_bucket=artifact_bucket,
        artifact_prefix=_optional_string(
            value, "artifact_prefix", "{sha}", f"{location}.artifact_prefix"
        ),
        targets=targets,
        invalidations=invalidations,
        repository=repository,
        services=services,
    )


def _commands(raw: object, location: str) -> tuple[tuple[str, ...], ...]:
    commands = []
    for index, command in enumerate(_sequence(raw, location)):
        commands.append(_string_tuple(command, f"{location}[{index}]"))
    return tuple(commands)


def _mapping(raw: object, location: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{location} must be a table")
    return raw


def _strict_mapping(raw: object, location: str, allowed: set[str]) -> Mapping[str, Any]:
    value = _mapping(raw, location)
    _reject_unknown(value, allowed, location)
    return value


def _reject_unknown(raw: Mapping[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(str(key) for key in set(raw) - allowed)
    if unknown:
        raise ConfigError(f"{location} contains unknown keys: {', '.join(unknown)}")


def _required_mapping(
    raw: Mapping[str, Any], key: str, location: str
) -> Mapping[str, Any]:
    if key not in raw:
        raise ConfigError(f"{location} is required")
    return _mapping(raw[key], location)


def _optional_mapping(
    raw: Mapping[str, Any], key: str, location: str
) -> Mapping[str, Any]:
    value = raw.get(key, {})
    return _mapping(value, location)


def _sequence(raw: object, location: str) -> Sequence[Any]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ConfigError(f"{location} must be an array")
    return raw


def _string(raw: object, location: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise ConfigError(f"{location} must be a non-empty string")
    return raw


def _required_string(raw: Mapping[str, Any], key: str, location: str) -> str:
    if key not in raw:
        raise ConfigError(f"{location} is required")
    return _string(raw[key], location)


def _optional_string(
    raw: Mapping[str, Any], key: str, default: str, location: str
) -> str:
    return _string(raw.get(key, default), location)


def _optional_nullable_string(
    raw: Mapping[str, Any], key: str, location: str
) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    return _string(value, location)


def _optional_bool(
    raw: Mapping[str, Any], key: str, default: bool, location: str
) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{location} must be a boolean")
    return value


def _string_tuple(
    raw: object, location: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    values = tuple(
        _string(item, f"{location}[{index}]")
        for index, item in enumerate(_sequence(raw, location))
    )
    if not values and not allow_empty:
        raise ConfigError(f"{location} must not be empty")
    return values


def _ensure_unique(values: Sequence[str] | Any, location: str) -> None:
    materialized = tuple(values)
    duplicates = sorted(
        {value for value in materialized if materialized.count(value) > 1}
    )
    if duplicates:
        raise ConfigError(f"{location} contains duplicates: {', '.join(duplicates)}")
