from __future__ import annotations

import unittest

from canadalogin_release.integrations.github import (
    COMMENT_MARKER,
    render_deployment_comment,
    sync_deployment_comment,
)
from canadalogin_release.pipeline.planner import Promotion


class FakeGitHub:
    def __init__(self, comments: list[dict[str, object]]) -> None:
        self.comments = comments
        self.requests: list[tuple[str, str, object]] = []

    def request(self, method: str, url: str, payload: object) -> object:
        self.requests.append((method, url, payload))
        if method == "GET":
            return self.comments
        if method == "POST":
            return {"id": 99}
        return None


class GitHubCommentTest(unittest.TestCase):
    def test_comment_lists_each_environment_and_version(self) -> None:
        comment = render_deployment_comment(
            (
                Promotion("staging", "1.2.2", "1.2.3"),
                Promotion("prod", "1.2.1", "1.2.3"),
            )
        )

        self.assertIn(COMMENT_MARKER, comment)
        self.assertIn("| `staging` | `1.2.2` | `1.2.3` |", comment)
        self.assertIn("| `prod` | `1.2.1` | `1.2.3` |", comment)

    def test_creates_updates_and_deletes_one_idempotent_comment(self) -> None:
        promotions = (Promotion("prod", "1.0.0", "1.1.0"),)
        github = FakeGitHub([])
        created = sync_deployment_comment(
            repository="cds-snc/example",
            pull_request=10,
            promotions=promotions,
            token="token",
            request_json=github.request,
        )
        self.assertEqual(created.action, "created")

        github = FakeGitHub([{"id": 42, "body": COMMENT_MARKER}])
        updated = sync_deployment_comment(
            repository="cds-snc/example",
            pull_request=10,
            promotions=promotions,
            token="token",
            request_json=github.request,
        )
        self.assertEqual(updated.action, "updated")
        self.assertEqual(github.requests[-1][0], "PATCH")

        github = FakeGitHub([{"id": 42, "body": COMMENT_MARKER}])
        deleted = sync_deployment_comment(
            repository="cds-snc/example",
            pull_request=10,
            promotions=(),
            token="token",
            request_json=github.request,
        )
        self.assertEqual(deleted.action, "deleted")
        self.assertEqual(github.requests[-1][0], "DELETE")


if __name__ == "__main__":
    unittest.main()
