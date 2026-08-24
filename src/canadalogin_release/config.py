from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


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
    }
)
DEPLOYMENT_WORKFLOW_SECRETS = frozenset(
    {
        "CLOUDFRONT_DISTRIBUTION_ID",
        "FRONTEND_APP_BUILD_ARTIFACTS_S3_BUCKET",
        "FRONTEND_APP_CLOUDFRONT_DISTRIBUTION_ID",
        "FRONTEND_APP_S3_BUCKET",
    }
)
SUPPORTED_WORKFLOW_SECRETS = frozenset(
    {
        *NOTIFICATION_WORKFLOW_SECRETS,
        *BUILD_WORKFLOW_SECRETS,
        *DEPLOYMENT_WORKFLOW_SECRETS,
    }
)

DEFAULT_DEPLOY_ENVIRONMENTS = ("dev", "test", "staging", "prod")
DEFAULT_NODE_VERSION = "22"
DEFAULT_PNPM_VERSION = "9.15.4"
SCHEMA_TWO_S3_ROLE = "RELEASE_S3_ROLE"
SCHEMA_TWO_ECS_ROLE = "RELEASE_ECS_ROLE"


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
            raise ConfigError(f"{location} must be a string or reference mapping")

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
            with config_path.open(encoding="utf-8") as config_file:
                raw = yaml.safe_load(config_file)
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise ConfigError(f"Unable to load {config_path}: {error}") from error
        if not isinstance(raw, Mapping):
            raise ConfigError(f"{config_path} must contain a mapping at the root")
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
    schema_version = raw.get("schema_version")
    if schema_version == 1:
        return _parse_pipeline_v1(raw)
    if schema_version == 2:
        return _parse_pipeline_v2(raw)
    raise ConfigError("schema_version must be 1 or 2")


def _parse_pipeline_v2(raw: Mapping[str, Any]) -> PipelineConfig:
    _reject_unknown(
        raw,
        {
            "schema_version",
            "application",
            "profile",
            "environments",
            "frontend",
            "backend",
            "site",
            "load_tests",
            "notifications",
            "hooks",
            "events",
        },
        "configuration",
    )
    application = _required_string(raw, "application", "application")
    profile = _required_string(raw, "profile", "profile")
    if profile not in {"ecs-service", "spa-ecs", "static-site"}:
        raise ConfigError("profile must be 'ecs-service', 'spa-ecs', or 'static-site'")

    deploy = _string_tuple(
        raw.get("environments", DEFAULT_DEPLOY_ENVIRONMENTS), "environments"
    )
    _ensure_unique(deploy, "environments")
    if "dev" not in deploy:
        raise ConfigError("environments must include 'dev'")
    environment_config = EnvironmentConfig(
        development="dev",
        deploy=deploy,
        versioned=tuple(environment for environment in deploy if environment != "dev"),
    )

    notifications = _parse_v2_notifications(raw)
    hooks = _parse_hooks(raw.get("hooks", {}))
    repository_dispatch = _parse_events(raw.get("events", {}), deploy)

    builds: list[BuildConfig] = []
    deployments: list[DeploymentConfig] = []
    if profile == "spa-ecs":
        frontend_build, frontend_deployment = _parse_v2_frontend(raw, deploy)
        builds.append(frontend_build)
        deployments.append(frontend_deployment)
    if profile in {"ecs-service", "spa-ecs"}:
        backend_build, backend_deployment = _parse_v2_backend(raw, application, deploy)
        builds.append(backend_build)
        deployments.append(backend_deployment)
    if profile == "static-site":
        site_build, site_deployment = _parse_v2_static_site(raw, deploy)
        builds.append(site_build)
        deployments.append(site_deployment)

    load_test = _parse_v2_load_tests(raw, application, deploy)
    if load_test is not None:
        builds.append(load_test)

    config = PipelineConfig(
        application=application,
        aws_region="ca-central-1",
        environments=environment_config,
        release=ReleaseConfig(),
        notifications=notifications,
        builds=tuple(builds),
        deployments=tuple(deployments),
        hooks=hooks,
        repository_dispatch=repository_dispatch,
    )
    _validate_pipeline_config(config)
    return config


def _parse_v2_notifications(raw: Mapping[str, Any]) -> NotificationConfig:
    notifications_raw = _optional_mapping(raw, "notifications", "notifications")
    _reject_unknown(
        notifications_raw,
        {"info_webhook", "alert_webhooks", "notify_development_failures"},
        "notifications",
    )
    info_raw = notifications_raw.get(
        "info_webhook", {"secret": "GC_SIGNIN_OPS_SLACK_INFO_WEBHOOK"}
    )
    alert_raw = notifications_raw.get(
        "alert_webhooks", [{"secret": "GC_SIGNIN_OPS_SLACK_ALERT_WEBHOOK"}]
    )
    return NotificationConfig(
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
                _sequence(alert_raw, "notifications.alert_webhooks")
            )
        ),
        notify_development_failures=_optional_bool(
            notifications_raw,
            "notify_development_failures",
            True,
            "notifications.notify_development_failures",
        ),
    )


def _parse_hooks(raw: object) -> HookConfig:
    hooks_raw = _mapping(raw, "hooks")
    _reject_unknown(
        hooks_raw,
        {"before_deploy", "health_check", "after_deploy", "on_failure"},
        "hooks",
    )
    return HookConfig(
        before_deploy=_commands(
            hooks_raw.get("before_deploy", ()), "hooks.before_deploy"
        ),
        health_check=_commands(hooks_raw.get("health_check", ()), "hooks.health_check"),
        after_deploy=_commands(hooks_raw.get("after_deploy", ()), "hooks.after_deploy"),
        on_failure=_commands(hooks_raw.get("on_failure", ()), "hooks.on_failure"),
    )


def _parse_events(raw: object, deploy: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    events_raw = _mapping(raw, "events")
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
    for event, targets in repository_dispatch.items():
        unknown_targets = set(targets) - set(deploy)
        if unknown_targets:
            raise ConfigError(
                f"events.repository_dispatch.{event} contains undeployable "
                "environments: " + ", ".join(sorted(unknown_targets))
            )
    return repository_dispatch


def _parse_v2_environment(raw: object, location: str) -> Mapping[str, ValueReference]:
    environment_raw = _mapping(raw, location)
    return {
        key: ValueReference.parse(value, f"{location}.{key}", BUILD_WORKFLOW_SECRETS)
        for key, value in environment_raw.items()
    }


def _contract_reference(name: str) -> ValueReference:
    return ValueReference(source="var", value=name)


def _service_variable_name(service: str, suffix: str, location: str) -> str:
    normalized = _string(service, location).replace("-", "_").upper()
    if not normalized.replace("_", "").isalnum():
        raise ConfigError(f"{location} must contain only letters, numbers, or hyphens")
    if normalized == "BACKEND":
        return f"RELEASE_ECS_{suffix}"
    return f"RELEASE_ECS_{normalized}_{suffix}"


def _parse_v2_backend(
    raw: Mapping[str, Any], application: str, deploy: tuple[str, ...]
) -> tuple[BuildConfig, DeploymentConfig]:
    backend_raw = _required_mapping(raw, "backend", "backend")
    _reject_unknown(backend_raw, {"dockerfile", "build_args", "services"}, "backend")
    dockerfile = Path(_required_string(backend_raw, "dockerfile", "backend.dockerfile"))
    build_args_raw = _optional_mapping(backend_raw, "build_args", "backend.build_args")
    build_args = {
        key: _string(value, f"backend.build_args.{key}")
        for key, value in build_args_raw.items()
    }
    service_names = _string_tuple(
        backend_raw.get("services", ("backend",)), "backend.services"
    )
    _ensure_unique(service_names, "backend.services")
    services = tuple(
        EcsServiceConfig(
            cluster=_contract_reference(
                _service_variable_name(service, "CLUSTER", f"backend.services[{index}]")
            ),
            service=_contract_reference(
                _service_variable_name(service, "SERVICE", f"backend.services[{index}]")
            ),
            container=_contract_reference(
                _service_variable_name(
                    service, "CONTAINER", f"backend.services[{index}]"
                )
            ),
            ssm_parameter="/ecs/{cluster}/{service}/container-image",
        )
        for index, service in enumerate(service_names)
    )
    return (
        BuildConfig(
            name="backend",
            kind="docker",
            environments=("dev",),
            aws_role=SCHEMA_TWO_ECS_ROLE,
            dns_audit=True,
            shared_artifact=True,
            docker=DockerBuildConfig(
                context=dockerfile.parent,
                dockerfile=dockerfile,
                repository=_contract_reference("RELEASE_ECR_REPOSITORY"),
                tags=("sha", "latest", "release"),
                build_args=build_args,
            ),
            sbom=SbomConfig(
                name=f"{application}-backend",
                dockerfile=dockerfile,
            ),
        ),
        DeploymentConfig(
            name="backend",
            kind="ecs",
            environments=deploy,
            aws_role=SCHEMA_TWO_ECS_ROLE,
            repository=_contract_reference("RELEASE_ECR_REPOSITORY"),
            services=services,
        ),
    )


def _parse_v2_frontend(
    raw: Mapping[str, Any], deploy: tuple[str, ...]
) -> tuple[BuildConfig, DeploymentConfig]:
    frontend_raw = _required_mapping(raw, "frontend", "frontend")
    _reject_unknown(
        frontend_raw,
        {
            "directory",
            "output",
            "package_manager",
            "package_manager_version",
            "environment",
            "invalidation_paths",
            "delete_stale_files",
        },
        "frontend",
    )
    directory = Path(
        _optional_string(frontend_raw, "directory", "frontend", "frontend.directory")
    )
    output = _optional_string(frontend_raw, "output", "dist", "frontend.output")
    package_manager = _optional_string(
        frontend_raw, "package_manager", "npm", "frontend.package_manager"
    )
    if package_manager not in {"npm", "pnpm"}:
        raise ConfigError("frontend.package_manager must be 'npm' or 'pnpm'")
    package_manager_version = _optional_string(
        frontend_raw,
        "package_manager_version",
        DEFAULT_PNPM_VERSION,
        "frontend.package_manager_version",
    )
    environment = _parse_v2_environment(
        frontend_raw.get("environment", {}), "frontend.environment"
    )
    delete_stale_files = _optional_bool(
        frontend_raw,
        "delete_stale_files",
        False,
        "frontend.delete_stale_files",
    )
    invalidation_paths = _string_tuple(
        frontend_raw.get("invalidation_paths", ("/index.html",)),
        "frontend.invalidation_paths",
        allow_empty=True,
    )
    if package_manager == "npm":
        working_directory = directory
        commands = (("npm", "ci"), ("npm", "run", "build"))
    else:
        working_directory = Path(".")
        commands = (
            ("corepack", "enable"),
            (
                "corepack",
                "prepare",
                f"pnpm@{package_manager_version}",
                "--activate",
            ),
            (
                "pnpm",
                "--dir",
                str(directory),
                "--ignore-workspace",
                "install",
                "--frozen-lockfile",
            ),
            (
                "pnpm",
                "--dir",
                str(directory),
                "--ignore-workspace",
                "build",
            ),
        )
    return (
        BuildConfig(
            name="frontend",
            kind="command",
            environments=deploy,
            aws_role=SCHEMA_TWO_S3_ROLE,
            node_version=DEFAULT_NODE_VERSION,
            working_directory=working_directory,
            commands=commands,
            environment=environment,
            s3_artifact=S3ArtifactConfig(
                source=directory / output,
                bucket=_contract_reference("RELEASE_FRONTEND_ARTIFACT_BUCKET"),
                delete=delete_stale_files,
                skip_if_exists_non_development=True,
            ),
        ),
        DeploymentConfig(
            name="frontend",
            kind="s3",
            environments=deploy,
            aws_role=SCHEMA_TWO_S3_ROLE,
            artifact_bucket=_contract_reference("RELEASE_FRONTEND_ARTIFACT_BUCKET"),
            targets=(
                S3TargetConfig(
                    bucket=_contract_reference("RELEASE_FRONTEND_BUCKET"),
                    delete=delete_stale_files,
                ),
            ),
            invalidations=(
                CloudFrontInvalidationConfig(
                    distribution=_contract_reference(
                        "RELEASE_FRONTEND_DISTRIBUTION_ID"
                    ),
                    paths=invalidation_paths,
                ),
            )
            if invalidation_paths
            else (),
        ),
    )


def _parse_v2_static_site(
    raw: Mapping[str, Any], deploy: tuple[str, ...]
) -> tuple[BuildConfig, DeploymentConfig]:
    site_raw = _required_mapping(raw, "site", "site")
    _reject_unknown(
        site_raw,
        {"directory", "output", "environment", "targets"},
        "site",
    )
    directory = Path(
        _optional_string(site_raw, "directory", "website", "site.directory")
    )
    output = _optional_string(site_raw, "output", "_site", "site.output")
    environment = _parse_v2_environment(
        site_raw.get("environment", {}), "site.environment"
    )
    targets_raw = _required_mapping(site_raw, "targets", "site.targets")
    if not targets_raw:
        raise ConfigError("site.targets must not be empty")
    targets: list[S3TargetConfig] = []
    invalidations: list[CloudFrontInvalidationConfig] = []
    for target_name, target_value in targets_raw.items():
        target_location = f"site.targets.{target_name}"
        target = _strict_mapping(
            target_value,
            target_location,
            {"delete_stale_files", "invalidation_paths"},
        )
        normalized_name = (
            _string(target_name, target_location).replace("-", "_").upper()
        )
        if not normalized_name.replace("_", "").isalnum():
            raise ConfigError(
                f"{target_location} must contain only letters, numbers, or hyphens"
            )
        delete_stale_files = _optional_bool(
            target,
            "delete_stale_files",
            True,
            f"{target_location}.delete_stale_files",
        )
        paths = _string_tuple(
            target.get("invalidation_paths", ("/*",)),
            f"{target_location}.invalidation_paths",
            allow_empty=True,
        )
        targets.append(
            S3TargetConfig(
                bucket=_contract_reference(f"RELEASE_SITE_{normalized_name}_BUCKET"),
                delete=delete_stale_files,
            )
        )
        if paths:
            invalidations.append(
                CloudFrontInvalidationConfig(
                    distribution=_contract_reference(
                        f"RELEASE_SITE_{normalized_name}_DISTRIBUTION_ID"
                    ),
                    paths=paths,
                )
            )
    return (
        BuildConfig(
            name="website",
            kind="command",
            environments=deploy,
            aws_role=SCHEMA_TWO_S3_ROLE,
            node_version=DEFAULT_NODE_VERSION,
            working_directory=directory,
            commands=(("npm", "ci"), ("npm", "run", "build")),
            environment=environment,
            s3_artifact=S3ArtifactConfig(
                source=directory / output,
                bucket=_contract_reference("RELEASE_STATIC_ARTIFACT_BUCKET"),
                delete=True,
                skip_if_exists_non_development=True,
            ),
        ),
        DeploymentConfig(
            name="website",
            kind="s3",
            environments=deploy,
            aws_role=SCHEMA_TWO_S3_ROLE,
            artifact_bucket=_contract_reference("RELEASE_STATIC_ARTIFACT_BUCKET"),
            targets=tuple(targets),
            invalidations=tuple(invalidations),
        ),
    )


def _parse_v2_load_tests(
    raw: Mapping[str, Any], application: str, deploy: tuple[str, ...]
) -> BuildConfig | None:
    if "load_tests" not in raw:
        return None
    load_tests_raw = raw["load_tests"]
    if isinstance(load_tests_raw, bool):
        enabled = load_tests_raw
        value: Mapping[str, Any] = {}
    else:
        value = _strict_mapping(load_tests_raw, "load_tests", {"enabled", "dockerfile"})
        enabled = _optional_bool(value, "enabled", True, "load_tests.enabled")
    if not enabled:
        return None
    if "staging" not in deploy:
        raise ConfigError("load_tests requires the staging environment")
    dockerfile = Path(
        _optional_string(
            value, "dockerfile", "load_tests/Dockerfile", "load_tests.dockerfile"
        )
    )
    return BuildConfig(
        name="load-test",
        kind="docker",
        environments=("staging",),
        aws_role=SCHEMA_TWO_ECS_ROLE,
        source_environment="staging",
        gates_deployment=False,
        docker=DockerBuildConfig(
            context=dockerfile.parent,
            dockerfile=dockerfile,
            repository=_contract_reference("RELEASE_LOAD_TEST_ECR_REPOSITORY"),
            tags=("sha", "latest"),
        ),
        sbom=SbomConfig(
            name=f"{application}-load-test",
            dockerfile=dockerfile,
        ),
    )


def _validate_pipeline_config(config: PipelineConfig) -> None:
    _ensure_unique((build.name for build in config.builds), "build names")
    _ensure_unique(
        (deployment.name for deployment in config.deployments), "deployment names"
    )
    deployable_environments = set(config.environments.deploy)
    for index, build in enumerate(config.builds):
        unknown_build_environments = set(build.environments) - deployable_environments
        if unknown_build_environments:
            raise ConfigError(
                f"builds[{index}].environments contains undeployable environments: "
                + ", ".join(sorted(unknown_build_environments))
            )
        if (
            build.source_environment
            and build.source_environment not in deployable_environments
        ):
            raise ConfigError(
                f"builds[{index}].source_environment "
                f"{build.source_environment!r} is not deployable"
            )
    for environment in config.environments.deploy:
        config.deployment_roles(environment)


def _parse_pipeline_v1(raw: Mapping[str, Any]) -> PipelineConfig:
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
    deployable_environments = set(deploy)
    for index, build in enumerate(builds):
        unknown_build_environments = set(build.environments) - deployable_environments
        if unknown_build_environments:
            raise ConfigError(
                f"builds[{index}].environments contains undeployable environments: "
                + ", ".join(sorted(unknown_build_environments))
            )
        if (
            build.source_environment
            and build.source_environment not in deployable_environments
        ):
            raise ConfigError(
                f"builds[{index}].source_environment "
                f"{build.source_environment!r} is not deployable"
            )

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
    for event, targets in repository_dispatch.items():
        unknown_targets = set(targets) - set(deploy)
        if unknown_targets:
            raise ConfigError(
                f"events.repository_dispatch.{event} contains undeployable "
                "environments: " + ", ".join(sorted(unknown_targets))
            )

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
        raise ConfigError(f"{location} must be a mapping")
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
        raise ConfigError(f"{location} must be a sequence")
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
