from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from canadalogin_release.cli import main


class CliTest(unittest.TestCase):
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
