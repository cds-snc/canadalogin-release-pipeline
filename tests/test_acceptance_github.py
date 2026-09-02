from __future__ import annotations

import unittest

from acceptance.github import (
    GitHubError,
    report_acceptance_result,
    request_acceptance_tests,
    validate_acceptance_request,
)

API_URL = "https://github.example/api/v3"
REPOSITORY = "owner/repository"
SHA = "a" * 40


def release_pull_request() -> dict[str, object]:
    return {
        "head": {
            "repo": {"full_name": REPOSITORY},
            "ref": "release-please--branches--main",
            "sha": SHA,
        },
        "base": {"ref": "main"},
        "state": "open",
        "user": {"type": "Bot"},
        "labels": [{"name": "autorelease: pending"}],
    }


class RequestRecorder:
    def __init__(self, responses: dict[tuple[str, str], object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, object]] = []

    def __call__(self, method: str, url: str, payload: object) -> object:
        self.calls.append((method, url, payload))
        response = self.responses.get((method, url))
        if isinstance(response, Exception):
            raise response
        return response


class AcceptanceGitHubTests(unittest.TestCase):
    def test_request_dispatches_current_release_pull_request(self) -> None:
        pull_url = f"{API_URL}/repos/{REPOSITORY}/pulls/42"
        permission_url = f"{API_URL}/repos/{REPOSITORY}/collaborators/alice/permission"
        dispatch_url = (
            f"{API_URL}/repos/{REPOSITORY}/actions/workflows/"
            "release-pipeline-tests.yml/dispatches"
        )
        recorder = RequestRecorder(
            {
                ("GET", pull_url): release_pull_request(),
                ("GET", permission_url): {"permission": "write"},
                ("POST", dispatch_url): None,
            }
        )

        request_acceptance_tests(
            comment_body="!test",
            commenter="alice",
            pull_request=42,
            repository=REPOSITORY,
            server_url="https://github.example",
            token="token",
            api_url=API_URL,
            request_json=recorder,
        )

        self.assertEqual(recorder.calls[2], (
            "POST",
            f"{API_URL}/repos/{REPOSITORY}/statuses/{SHA}",
            {
                "state": "pending",
                "context": "Integration / acceptance tests",
                "description": "Integration and acceptance tests are running.",
                "target_url": (
                    "https://github.example/owner/repository/actions/workflows/"
                    "release-pipeline-tests.yml"
                ),
            },
        ))
        self.assertEqual(recorder.calls[3], (
            "POST",
            dispatch_url,
            {
                "ref": "release-please--branches--main",
                "inputs": {"release-sha": SHA, "pull-request-number": "42"},
            },
        ))

    def test_request_marks_status_failed_when_dispatch_fails(self) -> None:
        pull_url = f"{API_URL}/repos/{REPOSITORY}/pulls/42"
        permission_url = f"{API_URL}/repos/{REPOSITORY}/collaborators/alice/permission"
        dispatch_url = (
            f"{API_URL}/repos/{REPOSITORY}/actions/workflows/"
            "release-pipeline-tests.yml/dispatches"
        )
        recorder = RequestRecorder(
            {
                ("GET", pull_url): release_pull_request(),
                ("GET", permission_url): {"permission": "write"},
                ("POST", dispatch_url): GitHubError("dispatch failed"),
            }
        )

        with self.assertRaisesRegex(GitHubError, "dispatch failed"):
            request_acceptance_tests(
                comment_body="!test",
                commenter="alice",
                pull_request=42,
                repository=REPOSITORY,
                server_url="https://github.example",
                token="token",
                api_url=API_URL,
                request_json=recorder,
            )

        self.assertEqual(recorder.calls[-1][2], {
            "state": "failure",
            "context": "Integration / acceptance tests",
            "description": "Integration and acceptance test dispatch failed.",
            "target_url": (
                "https://github.example/owner/repository/actions/workflows/"
                "release-pipeline-tests.yml"
            ),
        })

    def test_validation_requires_the_dispatched_head(self) -> None:
        recorder = RequestRecorder(
            {("GET", f"{API_URL}/repos/{REPOSITORY}/pulls/42"): release_pull_request()}
        )

        with self.assertRaisesRegex(GitHubError, "not the current"):
            validate_acceptance_request(
                pull_request=42,
                release_sha=SHA,
                repository=REPOSITORY,
                workflow_ref="release-please--branches--main",
                workflow_sha="b" * 40,
                token="token",
                api_url=API_URL,
                request_json=recorder,
            )

    def test_report_uses_all_suite_results(self) -> None:
        recorder = RequestRecorder({})

        report_acceptance_result(
            pull_request=42,
            release_sha=SHA,
            repository=REPOSITORY,
            run_url="https://github.example/owner/repository/actions/runs/1",
            validation_result="success",
            terraform_result="failure",
            acceptance_result="skipped",
            token="token",
            api_url=API_URL,
            request_json=recorder,
        )

        status_calls = recorder.calls[:2]
        self.assertEqual([call[2]["context"] for call in status_calls], [
            "Integration / acceptance tests",
            "release-gate",
        ])
        self.assertTrue(all(call[2]["state"] == "failure" for call in status_calls))
        self.assertEqual(
            recorder.calls[2][2],
            {"body": "Integration and acceptance tests failed. https://github.example/owner/repository/actions/runs/1"},
        )


if __name__ == "__main__":
    unittest.main()