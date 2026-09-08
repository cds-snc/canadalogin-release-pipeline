from __future__ import annotations

import unittest

from acceptance.github import (
    report_acceptance_result,
)

API_URL = "https://github.example/api/v3"
REPOSITORY = "owner/repository"
SHA = "a" * 40


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