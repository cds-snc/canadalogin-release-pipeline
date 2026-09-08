from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PENDING_CONTEXT = "Integration / acceptance tests"
JsonRequest = Callable[[str, str, Mapping[str, Any] | None], Any]


class GitHubError(RuntimeError):
    pass


def report_acceptance_result(
    *,
    pull_request: int,
    release_sha: str,
    repository: str,
    run_url: str,
    validation_result: str,
    terraform_result: str,
    acceptance_result: str,
    token: str,
    api_url: str,
    request_json: JsonRequest | None = None,
) -> None:
    succeeded = all(
        result == "success"
        for result in (validation_result, terraform_result, acceptance_result)
    )
    state = "success" if succeeded else "failure"
    result = "passed" if succeeded else "failed"
    description = f"Integration and acceptance tests {result}."
    client = GitHubClient(token, api_url, request_json)
    for context in (PENDING_CONTEXT, "release-gate"):
        client.create_status(
            repository,
            release_sha,
            state=state,
            context=context,
            description=description,
            target_url=run_url,
        )
    try:
        client.create_comment(
            repository,
            pull_request,
            f"Integration and acceptance tests {result}. {run_url}",
        )
    except GitHubError as error:
        print(
            "warning: Integration status was published, but the PR comment could not be posted: "
            f"{error}"
        )


class GitHubClient:
    def __init__(
        self,
        token: str,
        api_url: str,
        request_json: JsonRequest | None = None,
    ) -> None:
        if not token:
            raise GitHubError("GITHUB_TOKEN is not available")
        self.api_url = api_url.rstrip("/")
        self.request_json = request_json or _requester(token)

    def create_status(
        self,
        repository: str,
        sha: str,
        *,
        state: str,
        context: str,
        description: str,
        target_url: str,
    ) -> None:
        self.request_json(
            "POST",
            self._url(f"repos/{repository}/statuses/{sha}"),
            {
                "state": state,
                "context": context,
                "description": description,
                "target_url": target_url,
            },
        )

    def create_comment(self, repository: str, pull_request: int, body: str) -> None:
        self.request_json(
            "POST",
            self._url(f"repos/{repository}/issues/{pull_request}/comments"),
            {"body": body},
        )

    def _url(self, endpoint: str) -> str:
        return f"{self.api_url}/{endpoint}"


def _requester(token: str) -> JsonRequest:
    def request_json(method: str, url: str, payload: Mapping[str, Any] | None) -> Any:
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(
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
        try:
            with urlopen(request, timeout=15) as response:
                content = response.read()
        except (HTTPError, URLError, OSError) as error:
            raise GitHubError(f"GitHub API request failed: {error}") from error
        try:
            return json.loads(content) if content else None
        except json.JSONDecodeError as error:
            raise GitHubError(f"GitHub API returned invalid JSON: {error}") from error

    return request_json