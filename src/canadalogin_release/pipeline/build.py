from __future__ import annotations

import fnmatch
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ..config import BuildConfig, ConfigError, PipelineConfig
from ..support.commands import (
    GITHUB_CREDENTIALS,
    WORKFLOW_CREDENTIALS,
    CommandRunner,
    log,
)
from ..support.git import run_git
from ..support.runtime import (
    RuntimeContext,
    render,
    render_s3_prefix,
    resolve_reference,
)
from ..support.versions import deployment_sha, release_tag_for_sha

GitRunner = Callable[[Sequence[str], str | Path], str]
ReleaseTagResolver = Callable[[PipelineConfig, str, str | Path], str | None]


@dataclass(frozen=True)
class BuildResult:
    image_uri: str = ""
    image_digest: str = ""
    release_tag: str = ""
    release_version: str = ""
    source_sha: str = ""

    def github_outputs(self) -> dict[str, str]:
        return {
            "image_uri": self.image_uri,
            "image_digest": self.image_digest,
            "release_tag": self.release_tag,
            "release_version": self.release_version,
            "source_sha": self.source_sha,
        }


def execute_build(
    config: PipelineConfig,
    *,
    build_name: str,
    context: RuntimeContext,
    source_sha: str | None = None,
    runner: CommandRunner | None = None,
    git_runner: GitRunner = run_git,
    release_tag_resolver: ReleaseTagResolver = release_tag_for_sha,
) -> BuildResult:
    runner = runner or CommandRunner()
    build = _find_build(config, build_name)
    if context.environment not in build.environments:
        raise ConfigError(
            f"Build {build.name!r} is not configured for {context.environment!r}"
        )

    resolved_source_sha = source_sha or context.sha
    if source_sha is None and build.source_environment:
        resolved_source_sha = deployment_sha(
            config, build.source_environment, context.sha, context.repository
        )
    source_context = context
    if resolved_source_sha != context.sha:
        git_runner(["checkout", "--detach", resolved_source_sha], context.repository)
        source_context = replace(
            context,
            sha=resolved_source_sha,
            release_tag=release_tag_resolver(
                config, resolved_source_sha, context.repository
            ),
        )

    if build.kind == "command":
        image_uri = _execute_command_build(build, config, source_context, runner)
        image_digest = ""
    elif build.kind == "docker":
        image_uri, image_digest = _execute_docker_build(build, source_context, runner)
    else:
        raise ConfigError(f"Unsupported build kind {build.kind!r}")

    return BuildResult(
        image_uri=image_uri,
        image_digest=image_digest,
        release_tag=source_context.release_tag or "",
        release_version=source_context.release_version,
        source_sha=resolved_source_sha,
    )


def _execute_command_build(
    build: BuildConfig,
    config: PipelineConfig,
    context: RuntimeContext,
    runner: CommandRunner,
) -> str:
    command_environment = {
        name: resolve_reference(reference, context)
        for name, reference in build.environment.items()
    }
    working_directory = context.repository / build.working_directory
    for command in build.commands:
        runner.run(
            command,
            cwd=working_directory,
            environment=command_environment,
            unset_environment=WORKFLOW_CREDENTIALS,
        )

    if build.s3_artifact:
        artifact = build.s3_artifact
        bucket = resolve_reference(artifact.bucket, context)
        prefix = render_s3_prefix(artifact.prefix, context.template_values())
        destination = f"s3://{bucket}/{prefix}"
        listing = runner.run(
            ["aws", "s3", "ls", f"{destination}/"],
            check=False,
            log_output=False,
            unset_environment=GITHUB_CREDENTIALS,
        )
        if listing.returncode == 0 and listing.stdout.strip():
            log(f"Artifact already exists at {destination}; skipping upload.")
            return ""
        source = context.repository / artifact.source
        if not source.is_dir():
            raise ConfigError(f"Build artifact directory does not exist: {source}")
        log(f"Uploading build artifact from {source} to {destination}.")
        command = ["aws", "s3", "sync", str(source), destination]
        command.append("--only-show-errors")
        if artifact.delete:
            command.append("--delete")
        runner.run(
            command,
            unset_environment=GITHUB_CREDENTIALS,
        )
    return ""


def _execute_docker_build(
    build: BuildConfig, context: RuntimeContext, runner: CommandRunner
) -> tuple[str, str]:
    if build.docker is None:
        raise ConfigError(f"Docker build {build.name!r} has no docker configuration")
    docker = build.docker
    repository = resolve_reference(docker.repository, context)
    template_values = context.template_values(repository=repository)
    tags = _docker_tags(docker.tags, repository, context)
    if not tags:
        raise ConfigError(f"Docker build {build.name!r} produced no image tags")
    image_digest = ""
    actual_tags = tuple(tag.rsplit(":", 1)[1] for tag in tags)
    if set(docker.tags) & {"sha", "release"} and _parse_ecr_repository(repository)[0]:
        exclusion_patterns = _ensure_immutable_ecr_repository(
            repository, docker.tags, context.sha, runner
        )
        existing_digest = _ecr_image_digest(
            repository, context.sha, runner, missing_ok=True
        )
        if existing_digest:
            _validate_existing_ecr_tags(
                repository,
                actual_tags,
                context.sha,
                existing_digest,
                exclusion_patterns,
                runner,
            )
            log(
                f"ECR image {repository}:{context.sha} already exists; "
                "reusing the immutable image."
            )
            return f"{repository}:{context.sha}", existing_digest
        for tag in actual_tags:
            if tag == context.sha or _ecr_tag_is_excluded(tag, exclusion_patterns):
                continue
            if _ecr_image_digest(repository, tag, runner, missing_ok=True):
                raise ConfigError(
                    f"ECR image {repository}:{tag} already exists; immutable "
                    "tags cannot be rebuilt"
                )

    command = [
        "docker",
        "build",
        "--file",
        str(context.repository / docker.dockerfile),
    ]
    for name, value in docker.build_args.items():
        command.extend(["--build-arg", f"{name}={render(value, template_values)}"])
    for tag in tags:
        command.extend(["--tag", tag])
    command.append(str(context.repository / docker.context))
    runner.run(
        command,
        unset_environment=WORKFLOW_CREDENTIALS,
    )
    for tag in tags:
        runner.run(
            ["docker", "push", tag],
            unset_environment=WORKFLOW_CREDENTIALS,
        )
    if "sha" in docker.tags and _parse_ecr_repository(repository)[0]:
        image_digest = _ecr_image_digest(repository, context.sha, runner)
    return (
        f"{repository}:{context.sha}" if "sha" in docker.tags else tags[0],
        image_digest,
    )


def _ensure_immutable_ecr_repository(
    repository_uri: str,
    configured_tags: Sequence[str],
    sha_tag: str,
    runner: CommandRunner,
) -> tuple[str, ...]:
    registry_id, repository_name = _parse_ecr_repository(repository_uri)
    command = [
        "aws",
        "ecr",
        "describe-repositories",
        "--repository-names",
        repository_name,
        "--output",
        "json",
    ]
    if registry_id:
        command.extend(["--registry-id", registry_id])
    result = runner.run(
        command,
        check=False,
        log_output=False,
        unset_environment=GITHUB_CREDENTIALS,
    )
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError:
        document = None
    repositories = document.get("repositories") if isinstance(document, dict) else None
    repository = (
        repositories[0] if isinstance(repositories, list) and repositories else None
    )
    mutability = (
        repository.get("imageTagMutability") if isinstance(repository, dict) else None
    )
    filters = (
        repository.get("imageTagMutabilityExclusionFilters", [])
        if isinstance(repository, dict)
        else []
    )
    if result.returncode != 0 or mutability not in {
        "IMMUTABLE",
        "IMMUTABLE_WITH_EXCLUSION",
    }:
        state = str(mutability or "unavailable")
        raise ConfigError(
            f"ECR repository {repository_uri!r} is not immutable (state: {state})"
        )
    if not isinstance(filters, list):
        raise ConfigError(
            f"ECR repository {repository_uri!r} returned invalid mutability exclusions"
        )
    exclusion_patterns = [
        item.get("filter")
        for item in filters
        if isinstance(item, dict) and item.get("filterType") == "WILDCARD"
    ]
    if "sha" in configured_tags and any(
        isinstance(pattern, str) and fnmatch.fnmatch(sha_tag, pattern)
        for pattern in exclusion_patterns
    ):
        raise ConfigError(
            f"ECR repository {repository_uri!r} excludes SHA tags from immutability"
        )
    if "latest" in configured_tags and not any(
        isinstance(pattern, str) and fnmatch.fnmatch("latest", pattern)
        for pattern in exclusion_patterns
    ):
        raise ConfigError(
            f"ECR repository {repository_uri!r} must exclude latest from "
            "immutability when the build publishes latest"
        )
    return tuple(pattern for pattern in exclusion_patterns if isinstance(pattern, str))


def _validate_existing_ecr_tags(
    repository: str,
    tags: Sequence[str],
    sha_tag: str,
    image_digest: str,
    exclusion_patterns: Sequence[str],
    runner: CommandRunner,
) -> None:
    for tag in tags:
        if tag == sha_tag or _ecr_tag_is_excluded(tag, exclusion_patterns):
            continue
        tag_digest = _ecr_image_digest(repository, tag, runner, missing_ok=True)
        if tag_digest is None:
            raise ConfigError(
                f"ECR image {repository}:{tag} is missing for existing SHA "
                "image; refusing to skip the immutable tag"
            )
        if tag_digest != image_digest:
            raise ConfigError(
                f"ECR image {repository}:{tag} has digest {tag_digest!r}; "
                f"expected {image_digest!r} for SHA {sha_tag}"
            )


def _ecr_tag_is_excluded(tag: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(tag, pattern) for pattern in patterns)


def _parse_ecr_repository(repository_uri: str) -> tuple[str, str]:
    if "/" not in repository_uri:
        return "", repository_uri
    registry, repository_name = repository_uri.split("/", 1)
    if ".dkr.ecr." not in registry:
        return "", repository_uri
    registry_id = registry.split(".", 1)[0]
    return registry_id, repository_name


def _ecr_image_digest(
    repository_uri: str,
    image_tag: str,
    runner: CommandRunner,
    *,
    missing_ok: bool = False,
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
    result = runner.run(
        command,
        check=False,
        log_output=False,
        unset_environment=GITHUB_CREDENTIALS,
    )
    if result.returncode != 0:
        if missing_ok:
            return None
        raise ConfigError(
            f"ECR image {repository_uri}:{image_tag} could not be verified"
        )
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ConfigError(
            f"ECR returned invalid JSON for {repository_uri}:{image_tag}: {error}"
        ) from error
    details = document.get("imageDetails") if isinstance(document, dict) else None
    if not isinstance(details, list) or len(details) != 1:
        if missing_ok:
            return None
        raise ConfigError(f"ECR image {repository_uri}:{image_tag} does not exist")
    digest = details[0].get("imageDigest") if isinstance(details[0], dict) else None
    if not isinstance(digest, str) or not digest:
        raise ConfigError(
            f"ECR image {repository_uri}:{image_tag} returned no image digest"
        )
    return digest


def _docker_tags(
    configured_tags: tuple[str, ...], repository: str, context: RuntimeContext
) -> tuple[str, ...]:
    tags = []
    for tag in configured_tags:
        if tag == "sha":
            tags.append(f"{repository}:{context.sha}")
        elif tag == "latest":
            tags.append(f"{repository}:latest")
        elif tag == "release" and context.release_tag:
            tags.append(f"{repository}:{context.release_tag}")
    return tuple(tags)


def _find_build(config: PipelineConfig, name: str) -> BuildConfig:
    try:
        return next(build for build in config.builds if build.name == name)
    except StopIteration as error:
        raise ConfigError(f"Unknown build {name!r}") from error
