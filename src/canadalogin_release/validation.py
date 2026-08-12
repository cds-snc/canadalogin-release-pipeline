from __future__ import annotations

import fnmatch
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ConfigError, PipelineConfig
from .versions import parse_version_document, read_version


@dataclass(frozen=True)
class ValidationResult:
    warnings: tuple[str, ...]


def validate_repository(
    config: PipelineConfig, repository: str | Path = "."
) -> ValidationResult:
    root = Path(repository)
    for environment in config.environments.versioned:
        read_version(
            root / config.environments.version_directory / f"{environment}.json"
        )

    if config.release.enabled:
        _validate_release_please(config, root)

    warnings = []
    codeowners = _find_codeowners(root)
    if codeowners is None:
        warnings.append(
            "No CODEOWNERS file was found; verify a ruleset requires deployment approvals."
        )
    else:
        patterns = _codeowner_patterns(codeowners)
        for environment in config.environments.versioned:
            path = (
                config.environments.version_directory / f"{environment}.json"
            ).as_posix()
            if not any(fnmatch.fnmatch(path, pattern) for pattern in patterns):
                warnings.append(
                    f"{path} has no CODEOWNERS match; verify a ruleset requires deployment approvals."
                )
    return ValidationResult(tuple(warnings))


def _validate_release_please(config: PipelineConfig, root: Path) -> None:
    manifest_path = root / ".release-please-manifest.json"
    release_config_path = root / "release-please-config.json"
    manifest = _read_json_object(manifest_path)
    release_config = _read_json_object(release_config_path)

    version = manifest.get(".")
    if not isinstance(version, str):
        raise ConfigError(f"{manifest_path} must contain a string version for '.'")
    parse_version_document(json.dumps({"version": version}), str(manifest_path))

    if config.environments.versioned:
        first_environment = (
            "test"
            if "test" in config.environments.versioned
            else config.environments.versioned[0]
        )
        required_path = (
            config.environments.version_directory / f"{first_environment}.json"
        ).as_posix()
        if required_path not in _extra_file_paths(release_config):
            raise ConfigError(
                f"{release_config_path} must include {required_path!r} in extra-files"
            )


def _read_json_object(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text())
    except OSError as error:
        raise ConfigError(f"Unable to read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ConfigError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(value, Mapping):
        raise ConfigError(f"{path} must contain a JSON object")
    return value


def _extra_file_paths(release_config: Mapping[str, Any]) -> set[str]:
    paths = set()
    candidates: list[object] = [release_config.get("extra-files", ())]
    packages = release_config.get("packages")
    if isinstance(packages, Mapping):
        for package in packages.values():
            if isinstance(package, Mapping):
                candidates.append(package.get("extra-files", ()))
    for candidate in candidates:
        if not isinstance(candidate, Sequence) or isinstance(candidate, (str, bytes)):
            continue
        for item in candidate:
            if isinstance(item, str):
                paths.add(item)
            elif isinstance(item, Mapping) and isinstance(item.get("path"), str):
                paths.add(item["path"])
    return paths


def _find_codeowners(root: Path) -> Path | None:
    for path in (
        root / ".github" / "CODEOWNERS",
        root / "CODEOWNERS",
        root / "docs" / "CODEOWNERS",
    ):
        if path.is_file():
            return path
    return None


def _codeowner_patterns(path: Path) -> tuple[str, ...]:
    patterns = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) < 2 or not any(owner.startswith("@") for owner in fields[1:]):
            continue
        pattern = fields[0].lstrip("/")
        if pattern.endswith("/"):
            pattern += "*"
        patterns.append(pattern)
    return tuple(patterns)
