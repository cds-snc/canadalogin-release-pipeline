from __future__ import annotations

import json
import unittest
from pathlib import Path

from canadalogin_release.config import PipelineConfig
from canadalogin_release.notifications import (
    default_alert_webhook_values,
    notify,
    notify_pipeline_failure,
    pipeline_failure_webhook_values,
)
from canadalogin_release.runtime import RuntimeContext

EXAMPLES = Path(__file__).parents[1] / "examples"


class NotificationTest(unittest.TestCase):
    def test_schema_two_defaults_use_numbered_alert_slots(self) -> None:
        config = PipelineConfig.load(
            EXAMPLES / "gc-sign-in-migration" / "release-pipeline-configuration.yml"
        )
        context = RuntimeContext.create(
            repository=".",
            environment="dev",
            sha="abc123",
            release_tag=None,
            github_ref="",
            secrets={
                "RELEASE_PIPELINE_DEPLOY_ALERTS_SLACK_WEBHOOK": "https://hooks.example/base",
                "RELEASE_PIPELINE_DEPLOY_ALERTS_SLACK_WEBHOOK_1": "https://hooks.example/one",
                "RELEASE_PIPELINE_DEPLOY_ALERTS_SLACK_WEBHOOK_3": "https://hooks.example/three",
            },
        )
        requests: list[tuple[str, dict[str, str]]] = []

        result = notify(
            config,
            "build-failure",
            context,
            workflow_url="https://github.example/run/1",
            sender=lambda url, body: requests.append((url, json.loads(body))),
        )

        self.assertEqual(result.delivered, 2)
        self.assertEqual(
            [request[0] for request in requests],
            ["https://hooks.example/one", "https://hooks.example/three"],
        )

    def test_schema_two_defaults_use_info_webhook(self) -> None:
        config = PipelineConfig.load(
            EXAMPLES / "gc-sign-in-migration" / "release-pipeline-configuration.yml"
        )
        context = RuntimeContext.create(
            repository=".",
            environment="dev",
            sha="abc123",
            release_tag=None,
            github_ref="",
            secrets={
                "RELEASE_PIPELINE_DEPLOY_INFO_SLACK_WEBHOOK": "https://hooks.example/info",
            },
        )
        requests: list[tuple[str, dict[str, str]]] = []

        result = notify(
            config,
            "deploy-start",
            context,
            workflow_url="https://github.example/run/1",
            sender=lambda url, body: requests.append((url, json.loads(body))),
        )

        self.assertEqual(result.delivered, 1)
        self.assertEqual(requests[0][0], "https://hooks.example/info")

    def test_default_alert_webhook_uses_unsuffixed_secret_without_slots(self) -> None:
        self.assertEqual(
            default_alert_webhook_values(
                {
                    "RELEASE_PIPELINE_DEPLOY_ALERTS_SLACK_WEBHOOK": "https://hooks.example/base",
                }
            ),
            ("https://hooks.example/base",),
        )

    def test_pipeline_failure_defaults_use_numbered_alert_slots(self) -> None:
        self.assertEqual(
            pipeline_failure_webhook_values(
                {
                    "RELEASE_PIPELINE_DEPLOY_ALERTS_SLACK_WEBHOOK": "https://hooks.example/base",
                    "RELEASE_PIPELINE_DEPLOY_ALERTS_SLACK_WEBHOOK_2": "https://hooks.example/two",
                }
            ),
            ("https://hooks.example/two",),
        )

    def test_failure_names_environment_and_sends_every_alert_webhook(self) -> None:
        config = PipelineConfig.load(
            EXAMPLES
            / "gc-signin-user-selfservice-webapp"
            / "release-pipeline-configuration.yml"
        )
        context = RuntimeContext.create(
            repository=".",
            environment="dev",
            sha="abc123",
            release_tag=None,
            github_ref="",
            secrets={
                "RELEASE_PIPELINE_DEPLOY_ALERTS_SLACK_WEBHOOK_1": "https://hooks.example/one",
                "RELEASE_PIPELINE_DEPLOY_ALERTS_SLACK_WEBHOOK_2": "https://hooks.example/two",
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
                "https://hooks.example/one",
                "https://hooks.example/two",
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
