from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from canadalogin_release.cli import build_parser, main, run_migrate_ecs
from canadalogin_release.pipeline.deploy import DeploymentResult


class CliTest(unittest.TestCase):
    def test_migration_command_uses_planned_environment_and_sha(self) -> None:
        options = build_parser().parse_args(
            [
                "migrate-ecs",
                "--config",
                "config.yml",
                "--environment",
                "test",
                "--sha",
                "candidate-sha",
            ]
        )
        with (
            patch("canadalogin_release.cli._load_config") as load,
            patch(
                "canadalogin_release.cli.migrate_ecs",
                return_value=DeploymentResult("candidate-sha", (), ()),
            ) as migrate,
        ):
            self.assertEqual(run_migrate_ecs(options), 0)
        self.assertIs(migrate.call_args.args[0], load.return_value)
        self.assertEqual(migrate.call_args.args[1].environment, "test")
        self.assertEqual(migrate.call_args.args[1].sha, "candidate-sha")

    def test_validate_ignores_unrelated_deployment_boolean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "release-pipeline-configuration.yml"
            config.write_text(
                """
schema_version: 2
application: CLI test
profile: ecs-service
environments: [dev]
backend:
    dockerfile: Dockerfile
"""
            )
            with patch.dict(os.environ, {"RELEASE_FORCE_REDEPLOY": "invalid"}):
                result = main(["validate", "--config", str(config)])

        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
