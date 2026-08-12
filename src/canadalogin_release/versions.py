from __future__ import annotations

import json
import re
from pathlib import Path

from .config import ConfigError, PipelineConfig
from .git import file_at_revision, run_git

SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def parse_version_document(content: str, location: str) -> str:
    try:
        document = json.loads(content)
    except json.JSONDecodeError as error:
        raise ConfigError(f"{location} is not valid JSON: {error}") from error
    if not isinstance(document, dict) or set(document) != {"version"}:
        raise ConfigError(f"{location} must contain only a string 'version' property")
    version = document["version"]
    if not isinstance(version, str) or not SEMVER_PATTERN.fullmatch(version):
        raise ConfigError(f"{location} contains invalid semantic version {version!r}")
    return version


def read_version(path: str | Path) -> str:
    version_path = Path(path)
    try:
        content = version_path.read_text()
    except OSError as error:
        raise ConfigError(f"Unable to read {version_path}: {error}") from error
    return parse_version_document(content, str(version_path))


def version_at_revision(
    repository: str | Path, revision: str, path: str | Path
) -> str | None:
    content = file_at_revision(repository, revision, path)
    if content is None:
        return None
    return parse_version_document(content, f"{path} at {revision}")


def deployment_sha(
    config: PipelineConfig,
    environment: str,
    current_sha: str,
    repository: str | Path = ".",
    *,
    previous_revision: str = "",
) -> str:
    if environment == config.environments.development:
        if not current_sha:
            raise ConfigError("The current Git SHA is empty")
        return current_sha
    if environment not in config.environments.versioned:
        raise ConfigError(f"Environment {environment!r} has no version policy")

    version_path = config.environments.version_directory / f"{environment}.json"
    version = read_version(Path(repository) / version_path)
    tag = f"{config.release.tag_prefix}{version}"
    sha = run_git(["rev-list", "-n", "1", tag], repository, required=False)
    if not sha and previous_revision:
        manifest_path = Path(repository) / ".release-please-manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            manifest = {}
        previous_manifest_content = file_at_revision(
            repository, previous_revision, ".release-please-manifest.json"
        )
        try:
            previous_manifest = json.loads(previous_manifest_content or "{}")
        except json.JSONDecodeError:
            previous_manifest = {}
        head_sha = run_git(["rev-parse", "HEAD"], repository, required=False)
        if (
            manifest.get(".") == version
            and previous_manifest.get(".") != version
            and head_sha == current_sha
        ):
            return current_sha
    if not sha:
        raise ConfigError(f"Tag {tag!r} does not resolve to a commit")
    return sha


def release_tag_for_sha(
    config: PipelineConfig, sha: str, repository: str | Path = "."
) -> str | None:
    output = run_git(["tag", "--points-at", sha], repository)
    matching = sorted(
        tag
        for tag in output.splitlines()
        if tag.startswith(config.release.tag_prefix)
        and SEMVER_PATTERN.fullmatch(tag[len(config.release.tag_prefix) :])
    )
    return matching[0] if matching else None
