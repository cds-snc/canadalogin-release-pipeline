from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

RELEASE_BRANCH = "release-please--branches--main"
PENDING_CONTEXT = "Integration / acceptance tests"
JsonRequest = Callable[[str, str, Mapping[str, Any] | None], Any]


class GitHubError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleasePullRequest:
    head_repository: str
    head_ref: str
    head_sha: str
    base_branch: str
    state: str
    author_type: str
    labels: frozenset[str]


def request_acceptance_tests(
    *,
    comment_body: str,
    commenter: str,
    pull_request: int,
    repository: str,
    server_url: str,
    token: str,
    api_url: str,
    request_json: JsonRequest | None = None,
) -> None:
    if comment_body != "!test":
        return

    client = GitHubClient(token, api_url, request_json)
    release_pull_request = client.pull_request(repository, pull_request)
    validate_release_pull_request(release_pull_request, repository)
    permission = client.collaborator_permission(repository, commenter)
    if permission not in {"admin", "maintain", "push", "write"}:
        raise GitHubError(
            f"Commenter {commenter} does not have permission to request acceptance tests."
        )

    workflow_url = acceptance_workflow_url(server_url, repository)
    client.create_status(
        repository,
        release_pull_request.head_sha,
        state="pending",
        context=PENDING_CONTEXT,
        description="Integration and acceptance tests are running.",
        target_url=workflow_url,
    )
    try:
        client.dispatch_workflow(
            repository,
            ref=release_pull_request.head_ref,
            release_sha=release_pull_request.head_sha,
            pull_request=pull_request,
        )
    except GitHubError:
        try:
            client.create_status(
                repository,
                release_pull_request.head_sha,
                state="failure",
                context=PENDING_CONTEXT,
                description="Integration and acceptance test dispatch failed.",
                target_url=workflow_url,
            )
        except GitHubError:
            pass
        raise

    try:
        client.create_comment(
            repository,
            pull_request,
            f"Integration and acceptance tests dispatched for "
            f"{release_pull_request.head_sha}. {workflow_url}",
        )
    except GitHubError as error:
        print(f"warning: Integration tests were dispatched, but the PR comment could not be posted: {error}")


def validate_acceptance_request(
    *,
    pull_request: int,
    release_sha: str,
    repository: str,
    workflow_ref: str,
    workflow_sha: str,
    token: str,
    api_url: str,
    request_json: JsonRequest | None = None,
) -> None:
    release_pull_request = GitHubClient(token, api_url, request_json).pull_request(
        repository, pull_request
    )
    validate_release_pull_request(
        release_pull_request,
        repository,
        release_sha=release_sha,
        workflow_ref=workflow_ref,
        workflow_sha=workflow_sha,
    )


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


def validate_release_pull_request(
    pull_request: ReleasePullRequest,
    repository: str,
    *,
    release_sha: str = "",
    workflow_ref: str = "",
    workflow_sha: str = "",
) -> None:
    if (
        pull_request.head_repository != repository
        or pull_request.base_branch != "main"
        or pull_request.state != "open"
    ):
        raise GitHubError(
            "Acceptance tests require an open pull request in the release repository targeting main."
        )
    if (
        pull_request.head_ref != RELEASE_BRANCH
        or pull_request.author_type != "Bot"
        or "autorelease: pending" not in pull_request.labels
    ):
        raise GitHubError("Acceptance tests only run for release-please pull requests.")
    if release_sha and (
        pull_request.head_sha != release_sha
        or workflow_sha != release_sha
        or workflow_ref != pull_request.head_ref
    ):
        raise GitHubError(
            "The requested SHA is not the current release-please pull request head."
        )


def acceptance_workflow_url(server_url: str, repository: str) -> str:
    return f"{server_url.rstrip('/')}/{repository}/actions/workflows/release-pipeline-tests.yml"


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

    def pull_request(self, repository: str, pull_request: int) -> ReleasePullRequest:
        value = self.request_json("GET", self._url(f"repos/{repository}/pulls/{pull_request}"), None)
        if not isinstance(value, Mapping):
            raise GitHubError("GitHub pull request API returned an unexpected response")
        head = _mapping(value.get("head"))
        base = _mapping(value.get("base"))
        user = _mapping(value.get("user"))
        head_repository = _mapping(head.get("repo"))
        labels = value.get("labels")
        return ReleasePullRequest(
            head_repository=_string(head_repository.get("full_name")),
            head_ref=_string(head.get("ref")),
            head_sha=_string(head.get("sha")),
            base_branch=_string(base.get("ref")),
            state=_string(value.get("state")),
            author_type=_string(user.get("type")),
            labels=frozenset(
                _string(label.get("name"))
                for label in labels
                if isinstance(label, Mapping)
            )
            if isinstance(labels, list)
            else frozenset(),
        )

    def collaborator_permission(self, repository: str, username: str) -> str:
        value = self.request_json(
            "GET",
            self._url(f"repos/{repository}/collaborators/{username}/permission"),
            None,
        )
        return _string(value.get("permission")) if isinstance(value, Mapping) else ""

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

    def dispatch_workflow(
        self, repository: str, *, ref: str, release_sha: str, pull_request: int
    ) -> None:
        self.request_json(
            "POST",
            self._url(
                f"repos/{repository}/actions/workflows/release-pipeline-tests.yml/dispatches"
            ),
            {
                "ref": ref,
                "inputs": {
                    "release-sha": release_sha,
                    "pull-request-number": str(pull_request),
                },
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


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


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