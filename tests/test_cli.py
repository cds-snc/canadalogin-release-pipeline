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
            config = Path(directory) / "release-pipeline.toml"
            config.write_text(
                """
schema_version = 1
application = "CLI test"

[environments]
development = "dev"
deploy = ["dev"]
versioned = []

[release]
enabled = false
"""
            )
            with patch.dict(os.environ, {"RELEASE_FORCE_REDEPLOY": "invalid"}):
                result = main(["validate", "--config", str(config)])

        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
