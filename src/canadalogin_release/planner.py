from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import BuildConfig, ConfigError, PipelineConfig
from .git import changed_paths as git_changed_paths
from .versions import deployment_sha, read_version, version_at_revision

ShaResolver = Callable[[PipelineConfig, str, str, str | Path], str]
EnvironmentShaResolver = Callable[[str], str]


@dataclass(frozen=True)
class Promotion:
    environment: str
    from_version: str | None
    to_version: str | None


@dataclass(frozen=True)
class Plan:
    event_name: str
    release_please: bool
    force_redeploy: bool
    promotions: tuple[Promotion, ...]
    required_builds: tuple[dict[str, object], ...]
    auxiliary_builds: tuple[dict[str, object], ...]
    deployments: tuple[dict[str, object], ...]

    @property
    def target_environments(self) -> tuple[str, ...]:
        return tuple(str(item["environment"]) for item in self.deployments)

    def github_outputs(self) -> dict[str, str]:
        promotion_values = [asdict(promotion) for promotion in self.promotions]
        return {
            "release_please": _boolean(self.release_please),
            "force_redeploy": _boolean(self.force_redeploy),
            "has_promotions": _boolean(bool(self.promotions)),
            "has_required_builds": _boolean(bool(self.required_builds)),
            "has_auxiliary_builds": _boolean(bool(self.auxiliary_builds)),
            "has_deployments": _boolean(bool(self.deployments)),
            "promotion_environments": json.dumps(
                [promotion.environment for promotion in self.promotions],
                separators=(",", ":"),
            ),
            "promotions": json.dumps(promotion_values, separators=(",", ":")),
            "required_build_matrix": _matrix(
                self.required_builds, _empty_build_matrix_entry()
            ),
            "auxiliary_build_matrix": _matrix(
                self.auxiliary_builds, _empty_build_matrix_entry()
            ),
            "deployment_matrix": _matrix(
                self.deployments, _empty_deployment_matrix_entry()
            ),
        }


def create_plan(
    config: PipelineConfig,
    *,
    event_name: str,
    sha: str,
    repository: str | Path = ".",
    before_sha: str = "",
    manual_environment: str = "",
    force_redeploy: bool = False,
    rebuild: bool = False,
    repository_dispatch_event: str = "",
    changed_paths: Sequence[str] | None = None,
    sha_resolver: ShaResolver | None = None,
) -> Plan:
    repository_path = Path(repository)
    paths = (
        tuple(changed_paths)
        if changed_paths is not None
        else git_changed_paths(
            repository_path,
            before_sha,
            sha,
            config.environments.version_directory,
            three_dot=event_name in {"pull_request", "pull_request_target"},
        )
    )
    promotions = _promotions(config, repository_path, before_sha, paths)
    targets = _targets(
        config,
        event_name,
        manual_environment=manual_environment,
        repository_dispatch_event=repository_dispatch_event,
    )

    def resolve_sha(environment: str) -> str:
        if sha_resolver is not None:
            return sha_resolver(config, environment, sha, repository_path)
        return deployment_sha(
            config,
            environment,
            sha,
            repository_path,
            previous_revision=before_sha if event_name == "push" else "",
        )

    desired_shas = {environment: resolve_sha(environment) for environment in targets}

    should_build = event_name in {"push", "repository_dispatch"} or (
        event_name == "workflow_dispatch" and rebuild
    )
    required_builds: list[dict[str, object]] = []
    auxiliary_builds: list[dict[str, object]] = []
    if should_build:
        for build in config.builds:
            for environment, target_environment, source_sha in _planned_builds(
                build,
                event_name=event_name,
                targets=targets,
                workflow_sha=sha,
                desired_shas=desired_shas,
                resolve_sha=resolve_sha,
            ):
                entry = _build_matrix_entry(
                    config,
                    build,
                    environment=environment,
                    target_environment=target_environment,
                    source_sha=source_sha,
                    workflow_sha=sha,
                )
                destination = (
                    required_builds if build.gates_deployment else auxiliary_builds
                )
                destination.append(entry)
    if event_name == "workflow_dispatch":
        for build in config.builds:
            if build.gates_deployment or not build.source_environment:
                continue
            entry = _build_matrix_entry(
                config,
                build,
                environment=build.environments[0],
                target_environment=build.source_environment,
                source_sha=resolve_sha(build.source_environment),
                workflow_sha=sha,
            )
            if not any(
                existing["name"] == entry["name"] and existing["sha"] == entry["sha"]
                for existing in auxiliary_builds
            ):
                auxiliary_builds.append(entry)

    promoted_names = {promotion.environment for promotion in promotions}
    deployments = []
    for environment in targets:
        roles = config.deployment_roles(environment)
        deployments.append(
            {
                "environment": environment,
                "sha": desired_shas[environment],
                "enabled": True,
                "aws_region": config.aws_region,
                "s3_role": roles.get("s3", ""),
                "ecs_role": roles.get("ecs", ""),
                "notify": event_name == "workflow_dispatch"
                or environment in promoted_names,
                "notify_failure": True,
            }
        )

    return Plan(
        event_name=event_name,
        release_please=event_name == "push",
        force_redeploy=force_redeploy,
        promotions=promotions,
        required_builds=tuple(required_builds),
        auxiliary_builds=tuple(auxiliary_builds),
        deployments=tuple(deployments),
    )


def _planned_builds(
    build: BuildConfig,
    *,
    event_name: str,
    targets: Sequence[str],
    workflow_sha: str,
    desired_shas: dict[str, str],
    resolve_sha: EnvironmentShaResolver,
) -> tuple[tuple[str, str, str], ...]:
    entries: list[tuple[str, str, str]] = []
    if event_name == "push":
        for environment in build.environments:
            source_sha = (
                resolve_sha(build.source_environment)
                if build.source_environment
                else workflow_sha
            )
            entries.append((environment, environment, source_sha))
        return tuple(entries)

    if event_name == "repository_dispatch":
        for environment in build.environments:
            if environment not in targets:
                continue
            source_sha = (
                resolve_sha(build.source_environment)
                if build.source_environment
                else workflow_sha
            )
            entries.append((environment, environment, source_sha))
        return tuple(entries)

    if event_name != "workflow_dispatch":
        return ()

    for target in targets:
        if build.shared_artifact:
            credential_environment = build.environments[0]
        elif target in build.environments:
            credential_environment = target
        else:
            continue
        source_environment = build.source_environment or target
        source_sha = (
            desired_shas[target]
            if source_environment == target
            else resolve_sha(source_environment)
        )
        entry = (credential_environment, target, source_sha)
        duplicate_shared_artifact = build.shared_artifact and any(
            existing[0] == credential_environment and existing[2] == source_sha
            for existing in entries
        )
        if entry not in entries and not duplicate_shared_artifact:
            entries.append(entry)
    return tuple(entries)


def _build_matrix_entry(
    config: PipelineConfig,
    build: BuildConfig,
    *,
    environment: str,
    target_environment: str,
    source_sha: str,
    workflow_sha: str,
) -> dict[str, object]:
    return {
        "enabled": True,
        "name": build.name,
        "environment": environment,
        "target_environment": target_environment,
        "sha": source_sha,
        "notify_failure": True,
        "kind": build.kind,
        "aws_region": config.aws_region,
        "aws_role": build.aws_role or "",
        "dns_audit": build.dns_audit,
        "node_version": build.node_version or "",
        "sbom_enabled": build.sbom is not None and source_sha == workflow_sha,
        "sbom_name": build.sbom.name if build.sbom else "",
        "sbom_dockerfile": str(build.sbom.dockerfile) if build.sbom else "",
    }


def _targets(
    config: PipelineConfig,
    event_name: str,
    *,
    manual_environment: str,
    repository_dispatch_event: str,
) -> tuple[str, ...]:
    if event_name == "push":
        return config.environments.deploy
    if event_name in {"pull_request", "pull_request_target"}:
        return ()
    if event_name == "repository_dispatch":
        targets = config.repository_dispatch.get(
            repository_dispatch_event, (config.environments.development,)
        )
        return _validate_targets(config, targets)
    if event_name == "workflow_dispatch":
        selected = manual_environment or config.environments.development
        targets = config.environments.deploy if selected == "all" else (selected,)
        return _validate_targets(config, targets)
    raise ConfigError(f"Unsupported workflow event {event_name!r}")


def _validate_targets(
    config: PipelineConfig, targets: Sequence[str]
) -> tuple[str, ...]:
    unknown = set(targets) - set(config.environments.deploy)
    if unknown:
        raise ConfigError(
            "Unknown or disabled deployment environments: " + ", ".join(sorted(unknown))
        )
    return tuple(targets)


def _promotions(
    config: PipelineConfig,
    repository: Path,
    before_sha: str,
    paths: Sequence[str],
) -> tuple[Promotion, ...]:
    version_directory = config.environments.version_directory.as_posix().rstrip("/")
    changed_environments = set()
    for path in paths:
        candidate = Path(path)
        if (
            candidate.parent.as_posix() != version_directory
            or candidate.suffix != ".json"
        ):
            continue
        if candidate.stem in config.environments.versioned:
            changed_environments.add(candidate.stem)

    promotions = []
    for environment in config.environments.versioned:
        if environment not in changed_environments:
            continue
        path = config.environments.version_directory / f"{environment}.json"
        current_path = repository / path
        to_version = read_version(current_path) if current_path.exists() else None
        from_version = (
            version_at_revision(repository, before_sha, path) if before_sha else None
        )
        promotions.append(Promotion(environment, from_version, to_version))
    return tuple(promotions)


def _matrix(values: Sequence[dict[str, object]], empty_entry: dict[str, object]) -> str:
    return json.dumps({"include": list(values) or [empty_entry]}, separators=(",", ":"))


def _empty_build_matrix_entry() -> dict[str, object]:
    return {
        "enabled": False,
        "name": "",
        "environment": "",
        "target_environment": "",
        "sha": "",
        "notify_failure": False,
        "kind": "",
        "aws_region": "ca-central-1",
        "aws_role": "",
        "dns_audit": False,
        "node_version": "",
        "sbom_enabled": False,
        "sbom_name": "",
        "sbom_dockerfile": "",
    }


def _empty_deployment_matrix_entry() -> dict[str, object]:
    return {
        "enabled": False,
        "environment": "",
        "sha": "",
        "aws_region": "ca-central-1",
        "s3_role": "",
        "ecs_role": "",
        "notify": False,
        "notify_failure": False,
    }


def _boolean(value: bool) -> str:
    return "true" if value else "false"
