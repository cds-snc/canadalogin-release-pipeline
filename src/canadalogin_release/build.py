from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from .commands import CommandRunner
from .config import BUILD_WORKFLOW_SECRETS, BuildConfig, ConfigError, PipelineConfig
from .git import run_git
from .runtime import RuntimeContext, render, render_s3_prefix, resolve_reference
from .versions import deployment_sha, release_tag_for_sha

GitRunner = Callable[[Sequence[str], str | Path], str]
ReleaseTagResolver = Callable[[PipelineConfig, str, str | Path], str | None]


@dataclass(frozen=True)
class BuildResult:
    image_uri: str = ""
    release_tag: str = ""
    release_version: str = ""
    source_sha: str = ""

    def github_outputs(self) -> dict[str, str]:
        return {
            "image_uri": self.image_uri,
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
    elif build.kind == "docker":
        image_uri = _execute_docker_build(build, source_context, runner)
    else:
        raise ConfigError(f"Unsupported build kind {build.kind!r}")

    return BuildResult(
        image_uri=image_uri,
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
            unset_environment=BUILD_WORKFLOW_SECRETS,
        )

    if build.s3_artifact:
        artifact = build.s3_artifact
        bucket = resolve_reference(artifact.bucket, context)
        prefix = render_s3_prefix(artifact.prefix, context.template_values())
        destination = f"s3://{bucket}/{prefix}"
        if artifact.skip_if_exists_non_development and (
            context.environment != config.environments.development
        ):
            listing = runner.run(
                ["aws", "s3", "ls", f"{destination}/"],
                check=False,
                unset_environment=BUILD_WORKFLOW_SECRETS,
            )
            if listing.returncode == 0 and listing.stdout.strip():
                print(f"Artifact already exists at {destination}; skipping upload.")
                return ""
        source = context.repository / artifact.source
        if not source.is_dir():
            raise ConfigError(f"Build artifact directory does not exist: {source}")
        command = ["aws", "s3", "sync", str(source), destination]
        if artifact.delete:
            command.append("--delete")
        runner.run(command, unset_environment=BUILD_WORKFLOW_SECRETS)
    return ""


def _execute_docker_build(
    build: BuildConfig, context: RuntimeContext, runner: CommandRunner
) -> str:
    if build.docker is None:
        raise ConfigError(f"Docker build {build.name!r} has no docker configuration")
    docker = build.docker
    repository = resolve_reference(docker.repository, context)
    template_values = context.template_values(repository=repository)
    tags = _docker_tags(docker.tags, repository, context)
    if not tags:
        raise ConfigError(f"Docker build {build.name!r} produced no image tags")

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
    runner.run(command, unset_environment=BUILD_WORKFLOW_SECRETS)
    for tag in tags:
        runner.run(["docker", "push", tag], unset_environment=BUILD_WORKFLOW_SECRETS)
    return f"{repository}:{context.sha}" if "sha" in docker.tags else tags[0]


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
