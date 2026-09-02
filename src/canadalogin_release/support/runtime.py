# This is a data class and helpers regarding the context of the deployment,
# including the repository, environment, commit SHA, release tag, GitHub reference.
# It also provides functions to resolve and render template values based on this context.

from __future__ import annotations

import json
import os
import string
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..config import ConfigError, ValueReference


@dataclass(frozen=True)
class RuntimeContext:
    repository: Path
    environment: str
    sha: str
    release_tag: str | None
    github_ref: str
    variables: Mapping[str, str]
    build_timestamp: str

    @property
    def release_version(self) -> str:
        return (
            self.release_tag
            or f"{self.build_timestamp[:10].replace('-', '')}-{self.sha}"
        )

    def template_values(self, *, repository: str = "") -> dict[str, str]:
        return {
            "sha": self.sha,
            "environment": self.environment,
            "release_tag": self.release_tag or "",
            "release_version": self.release_version,
            "build_timestamp": self.build_timestamp,
            "github_ref": self.github_ref,
            "repository": repository,
            "aws_account_id": os.environ.get("AWS_ACCOUNT_ID", ""),
            "aws_region": os.environ.get(
                "AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "")
            ),
        }

    @classmethod
    def create(
        cls,
        *,
        repository: str | Path,
        environment: str,
        sha: str,
        release_tag: str | None,
        github_ref: str,
        variables: Mapping[str, str] | None = None,
        now: datetime | None = None,
    ) -> RuntimeContext:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        return cls(
            repository=Path(repository),
            environment=environment,
            sha=sha,
            release_tag=release_tag,
            github_ref=github_ref,
            variables=variables or {},
            build_timestamp=timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )


def resolve_reference(
    reference: ValueReference,
    context: RuntimeContext,
    *,
    repository: str = "",
) -> str:
    value = reference.resolve(context.variables)
    return render(value, context.template_values(repository=repository))


def render(value: str, values: Mapping[str, str]) -> str:
    fields = {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(value)
        if field_name is not None
    }
    unknown = fields - set(values)
    if unknown:
        raise ConfigError(f"Unknown template fields: {', '.join(sorted(unknown))}")
    try:
        return value.format_map(values)
    except (KeyError, ValueError) as error:
        raise ConfigError(f"Unable to render template {value!r}: {error}") from error


def render_s3_prefix(value: str, values: Mapping[str, str]) -> str:
    prefix = render(value, values).strip("/")
    if not prefix:
        raise ConfigError(
            f"S3 prefix template {value!r} rendered empty; bucket-root artifacts are forbidden"
        )
    return prefix


def variables_from_environment() -> dict[str, str]:
    raw = os.environ.get("RELEASE_PIPELINE_VARS", "{}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ConfigError(
            f"RELEASE_PIPELINE_VARS is not valid JSON: {error}"
        ) from error
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value.items()
    ):
        raise ConfigError("RELEASE_PIPELINE_VARS must be a JSON object of strings")
    return value
