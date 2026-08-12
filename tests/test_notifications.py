from __future__ import annotations

import json
import unittest
from pathlib import Path

from canadalogin_release.config import PipelineConfig
from canadalogin_release.notifications import notify, notify_pipeline_failure
from canadalogin_release.runtime import RuntimeContext

EXAMPLES = Path(__file__).parents[1] / "examples"


class NotificationTest(unittest.TestCase):
    def test_failure_names_environment_and_sends_every_alert_webhook(self) -> None:
        config = PipelineConfig.load(
            EXAMPLES / "gc-signin-user-selfservice-webapp" / "release-pipeline.toml"
        )
        context = RuntimeContext.create(
            repository=".",
            environment="dev",
            sha="abc123",
            release_tag=None,
            github_ref="",
            secrets={
                "GC_SIGNIN_OPS_SLACK_ALERT_WEBHOOK": "https://hooks.example/ops",
                "CL_DEV_SLACK_ALERT_WEBHOOK": "https://hooks.example/dev",
            },
        )
        requests: list[tuple[str, dict[str, str]]] = []

        result = notify(
            config,
            "build-failure",
            context,
            workflow_url="https://github.example/run/1",
            detail="frontend",
            sender=lambda url, body: requests.append((url, json.loads(body))),
        )

        self.assertEqual(result.delivered, 2)
        self.assertEqual(
            [request[0] for request in requests],
            [
                "https://hooks.example/ops",
                "https://hooks.example/dev",
            ],
        )
        self.assertIn("`dev`", requests[0][1]["text"])
        self.assertIn("frontend", requests[0][1]["text"])

    def test_pipeline_failure_does_not_require_configuration(self) -> None:
        requests: list[tuple[str, dict[str, str]]] = []

        result = notify_pipeline_failure(
            "cds-snc/example",
            "https://github.example/run/1",
            ["https://hooks.example/ops", "", "https://hooks.example/ops"],
            sender=lambda url, body: requests.append((url, json.loads(body))),
        )

        self.assertEqual(result.delivered, 1)
        self.assertIn("planning or release-please", requests[0][1]["text"])


if __name__ == "__main__":
    unittest.main()
