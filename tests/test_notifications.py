from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from canadalogin_release.config import PipelineConfig
from canadalogin_release.notifications import (
    alert_webhook_values,
    default_alert_webhook_values,
    default_info_webhook_values,
    notify,
    notify_pipeline_failure,
    pipeline_failure_webhook_values,
)
from canadalogin_release.runtime import RuntimeContext

EXAMPLES = Path(__file__).parents[1] / "examples"


class NotificationTest(unittest.TestCase):
    def test_alert_webhook_override_takes_precedence_over_secret_slots(self) -> None:
        self.assertEqual(
            alert_webhook_values(
                {
                    "RELEASE_PIPELINE_DEPLOY_ALERTS_SLACK_WEBHOOK_OVERRIDE": "https://capture.example/test",
                    "RELEASE_PIPELINE_DEPLOY_ALERTS_SLACK_WEBHOOK_1": "https://hooks.example/one",
                }
            ),
            ("https://capture.example/test",),
        )

    def test_schema_two_defaults_use_numbered_alert_slots(self) -> None:
        config = PipelineConfig.load(
            EXAMPLES
            / "canadalogin-user-selfservice-webapp"
            / "release-pipeline-configuration.yml"
        )
        context = RuntimeContext.create(
            repository=".",
            environment="dev",
            sha="abc123",
            release_tag=None,
            github_ref="",
        )
        requests: list[tuple[str, dict[str, str]]] = []

        with patch.dict(
            os.environ,
            {
                "RELEASE_PIPELINE_DEPLOY_ALERTS_SLACK_WEBHOOK": "https://hooks.example/base",
                "RELEASE_PIPELINE_DEPLOY_ALERTS_SLACK_WEBHOOK_1": "https://hooks.example/one",
                "RELEASE_PIPELINE_DEPLOY_ALERTS_SLACK_WEBHOOK_3": "https://hooks.example/three",
            },
            clear=True,
        ):
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
            EXAMPLES
            / "canadalogin-user-selfservice-webapp"
            / "release-pipeline-configuration.yml"
        )
        context = RuntimeContext.create(
            repository=".",
            environment="dev",
            sha="abc123",
            release_tag=None,
            github_ref="",
        )
        requests: list[tuple[str, dict[str, str]]] = []

        with patch.dict(
            os.environ,
            {
                "RELEASE_PIPELINE_DEPLOY_INFO_SLACK_WEBHOOK": "https://hooks.example/info",
            },
            clear=True,
        ):
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

    def test_default_info_webhook_uses_unsuffixed_secret_without_slots(self) -> None:
        self.assertEqual(
            default_info_webhook_values(
                {
                    "RELEASE_PIPELINE_DEPLOY_INFO_SLACK_WEBHOOK": "https://hooks.example/base",
                }
            ),
            ("https://hooks.example/base",),
        )

    def test_info_and_alert_defaults_use_the_same_numbered_slots(self) -> None:
        cases = (
            (
                default_info_webhook_values,
                "RELEASE_PIPELINE_DEPLOY_INFO_SLACK_WEBHOOK",
            ),
            (
                default_alert_webhook_values,
                "RELEASE_PIPELINE_DEPLOY_ALERTS_SLACK_WEBHOOK",
            ),
        )

        for resolve, base_secret in cases:
            with self.subTest(base_secret=base_secret):
                self.assertEqual(
                    resolve(
                        {
                            base_secret: "https://hooks.example/base",
                            f"{base_secret}_1": "https://hooks.example/one",
                            f"{base_secret}_3": "https://hooks.example/three",
                        }
                    ),
                    (
                        "https://hooks.example/one",
                        "https://hooks.example/three",
                    ),
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
            / "canadalogin-user-selfservice-webapp"
            / "release-pipeline-configuration.yml"
        )
        context = RuntimeContext.create(
            repository=".",
            environment="dev",
            sha="abc123",
            release_tag=None,
            github_ref="",
        )
        requests: list[tuple[str, dict[str, str]]] = []

        with patch.dict(
            os.environ,
            {
                "RELEASE_PIPELINE_DEPLOY_ALERTS_SLACK_WEBHOOK_1": "https://hooks.example/one",
                "RELEASE_PIPELINE_DEPLOY_ALERTS_SLACK_WEBHOOK_2": "https://hooks.example/two",
            },
            clear=True,
        ):
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
