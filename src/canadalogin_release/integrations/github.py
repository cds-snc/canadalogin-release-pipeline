from __future__ import annotations

import json
import re
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..config import ConfigError
from ..pipeline.planner import Promotion

COMMENT_MARKER = "<!-- canadalogin-release-deployment-impact -->"
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class CommentResult:
    action: str
    comment_id: int | None


def render_deployment_comment(promotions: Sequence[Promotion]) -> str:
    rows = []
    for promotion in promotions:
        previous = promotion.from_version or "not deployed"
        desired = promotion.to_version or "removed"
        rows.append(f"| `{promotion.environment}` | `{previous}` | `{desired}` |")
    table = "\n".join(rows)
    return (
        f"{COMMENT_MARKER}\n"
        "## Deployment impact\n\n"
        "Merging this pull request changes the desired deployment state. "
        "Confirm the environment and version changes before approving.\n\n"
        "| Environment | Current version | Desired version |\n"
        "| --- | --- | --- |\n"
        f"{table}\n"
    )


def sync_deployment_comment(
    *,
    repository: str,
    pull_request: int,
    promotions: Sequence[Promotion],
    token: str,
    api_url: str = "https://api.github.com",
    request_json: Callable[[str, str, Mapping[str, Any] | None], Any] | None = None,
) -> CommentResult:
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise ConfigError(f"Invalid GitHub repository name {repository!r}")
    if pull_request <= 0:
        raise ConfigError("Pull request number must be positive")
    if not token:
        raise ConfigError("GITHUB_TOKEN is not available")

    request = request_json or _requester(token)
    comments_url = f"{api_url}/repos/{repository}/issues/{pull_request}/comments"
    comments = request("GET", f"{comments_url}?per_page=100", None)
    if not isinstance(comments, list):
        raise ConfigError("GitHub comments API returned an unexpected response")
    existing = next(
        (
            comment
            for comment in comments
            if isinstance(comment, Mapping)
            and isinstance(comment.get("body"), str)
            and COMMENT_MARKER in comment["body"]
        ),
        None,
    )
    existing_id = existing.get("id") if existing else None
    if existing_id is not None and not isinstance(existing_id, int):
        raise ConfigError("GitHub comment ID is invalid")

    if not promotions:
        if existing_id is None:
            return CommentResult("unchanged", None)
        request(
            "DELETE",
            f"{api_url}/repos/{repository}/issues/comments/{existing_id}",
            None,
        )
        return CommentResult("deleted", existing_id)

    body = render_deployment_comment(promotions)
    if existing_id is not None:
        request(
            "PATCH",
            f"{api_url}/repos/{repository}/issues/comments/{existing_id}",
            {"body": body},
        )
        return CommentResult("updated", existing_id)
    created = request("POST", comments_url, {"body": body})
    created_id = created.get("id") if isinstance(created, Mapping) else None
    if not isinstance(created_id, int):
        raise ConfigError("Creating the GitHub comment returned no comment ID")
    return CommentResult("created", created_id)


def promotions_from_json(value: str) -> tuple[Promotion, ...]:
    try:
        raw = json.loads(value)
    except json.JSONDecodeError as error:
        raise ConfigError(f"Promotions are not valid JSON: {error}") from error
    if not isinstance(raw, list):
        raise ConfigError("Promotions must be a JSON array")
    promotions = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping) or not isinstance(
            item.get("environment"), str
        ):
            raise ConfigError(f"Promotion {index} is invalid")
        from_version = item.get("from_version")
        to_version = item.get("to_version")
        if from_version is not None and not isinstance(from_version, str):
            raise ConfigError(f"Promotion {index} has an invalid from_version")
        if to_version is not None and not isinstance(to_version, str):
            raise ConfigError(f"Promotion {index} has an invalid to_version")
        promotions.append(Promotion(item["environment"], from_version, to_version))
    return tuple(promotions)


def _requester(
    token: str,
) -> Callable[[str, str, Mapping[str, Any] | None], Any]:
    def request_json(method: str, url: str, payload: Mapping[str, Any] | None) -> Any:
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method=method,
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            content = response.read()
            return json.loads(content) if content else None

    return request_json
