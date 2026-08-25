from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

VERIFY_PATH = Path(__file__).parents[1] / "acceptance" / "scripts" / "verify.py"
VERIFY_SPEC = importlib.util.spec_from_file_location("acceptance_verify", VERIFY_PATH)
if VERIFY_SPEC is None or VERIFY_SPEC.loader is None:
    raise RuntimeError(f"Unable to load {VERIFY_PATH}")
VERIFY = importlib.util.module_from_spec(VERIFY_SPEC)
VERIFY_SPEC.loader.exec_module(VERIFY)


class AcceptanceVerifyTest(unittest.TestCase):
    def test_scenario_uses_full_ecr_uri_for_live_identity_checks(self) -> None:
        scenario = VERIFY.scenario_for_account(
            VERIFY.SCENARIOS["standard-ecs"],
            "123456789012",
            "us-east-1",
        )

        self.assertEqual(
            scenario["ecr_uri"],
            "123456789012.dkr.ecr.us-east-1.amazonaws.com/cl-acceptance-standard",
        )

    def test_target_health_waits_for_a_healthy_target_while_old_targets_drain(
        self,
    ) -> None:
        with (
            patch.object(
                VERIFY,
                "aws_json",
                side_effect=[
                    {
                        "TargetHealthDescriptions": [
                            {"TargetHealth": {"State": "draining"}}
                        ]
                    },
                    {
                        "TargetHealthDescriptions": [
                            {"TargetHealth": {"State": "healthy"}}
                        ]
                    },
                ],
            ) as aws_json,
            patch.object(VERIFY.time, "sleep") as sleep,
        ):
            VERIFY.verify_target_health("target-group-arn")

        self.assertEqual(aws_json.call_count, 2)
        sleep.assert_called_once_with(5)

    def test_failure_hook_verification_requires_expected_outcome_validation(
        self,
    ) -> None:
        jobs = [
            {
                "jobs": [
                    {
                        "steps": [
                            {
                                "name": "Validate health-check result",
                                "conclusion": "success",
                            },
                            {"name": "Run failure hooks", "conclusion": "success"},
                        ]
                    }
                ]
            }
        ]

        with (
            patch.dict(
                VERIFY.os.environ,
                {"GITHUB_REPOSITORY": "example/repository", "RUN_ID": "123"},
            ),
            patch.object(VERIFY, "command", return_value=json.dumps(jobs)),
        ):
            VERIFY.verify_failure_hook()


if __name__ == "__main__":
    unittest.main()
