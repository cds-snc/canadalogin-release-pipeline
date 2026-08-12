from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from canadalogin_release.config import ConfigError, PipelineConfig
from canadalogin_release.validation import validate_repository

CONFIG = """
schema_version = 1
application = "Validation example"

[environments]
development = "dev"
deploy = ["dev", "test", "prod"]
versioned = ["test", "prod"]
"""


class ValidationTest(unittest.TestCase):
    def repository(self) -> tuple[tempfile.TemporaryDirectory, PipelineConfig]:
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        config_path = root / ".github" / "release-pipeline.toml"
        config_path.parent.mkdir()
        config_path.write_text(CONFIG)
        versions = root / ".deployed_versions"
        versions.mkdir()
        for environment in ("test", "prod"):
            (versions / f"{environment}.json").write_text(
                json.dumps({"version": "1.2.3"})
            )
        (root / ".release-please-manifest.json").write_text(json.dumps({".": "1.2.3"}))
        (root / "release-please-config.json").write_text(
            json.dumps({"extra-files": [".deployed_versions/test.json"]})
        )
        return directory, PipelineConfig.load(config_path)

    def test_validates_release_files_and_codeowners(self) -> None:
        directory, config = self.repository()
        with directory:
            root = Path(directory.name)
            (root / ".github" / "CODEOWNERS").write_text(
                ".deployed_versions/* @cds-snc/deploy-approvers\n"
            )

            result = validate_repository(config, root)

        self.assertEqual(result.warnings, ())

    def test_warns_when_ownership_is_not_locally_declared(self) -> None:
        directory, config = self.repository()
        with directory:
            result = validate_repository(config, directory.name)

        self.assertEqual(len(result.warnings), 1)

    def test_rejects_release_config_that_does_not_advance_test(self) -> None:
        directory, config = self.repository()
        with directory:
            root = Path(directory.name)
            (root / "release-please-config.json").write_text(
                json.dumps({"extra-files": ["README.md"]})
            )
            with self.assertRaisesRegex(ConfigError, "must include"):
                validate_repository(config, root)


if __name__ == "__main__":
    unittest.main()
