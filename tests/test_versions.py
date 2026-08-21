from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from canadalogin_release.config import ConfigError, PipelineConfig
from canadalogin_release.versions import deployment_sha


class VersionsTest(unittest.TestCase):
    def git(self, repository: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_push_can_resolve_current_release_before_tag_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            self.git(repository, "init", "-b", "main")
            self.git(repository, "config", "user.name", "Release Test")
            self.git(repository, "config", "user.email", "release@example.invalid")
            config_path = repository / "release-pipeline-configuration.yml"
            config_path.write_text(
                """
schema_version: 1
application: Release test

environments:
    development: dev
    deploy: [dev, test]
    versioned: [test]
"""
            )
            versions = repository / ".deployed_versions"
            versions.mkdir()
            (versions / "test.json").write_text(json.dumps({"version": "1.0.0"}))
            (repository / ".release-please-manifest.json").write_text(
                json.dumps({".": "1.0.0"})
            )
            self.git(repository, "add", ".")
            self.git(repository, "commit", "-m", "release 1.0.0")
            previous_sha = self.git(repository, "rev-parse", "HEAD")

            (versions / "test.json").write_text(json.dumps({"version": "2.0.0"}))
            (repository / ".release-please-manifest.json").write_text(
                json.dumps({".": "2.0.0"})
            )
            self.git(repository, "add", ".")
            self.git(repository, "commit", "-m", "release 2.0.0")
            release_sha = self.git(repository, "rev-parse", "HEAD")
            config = PipelineConfig.load(config_path)

            pending_sha = deployment_sha(
                config,
                "test",
                release_sha,
                repository,
                previous_revision=previous_sha,
            )

            (repository / "README.md").write_text("later push\n")
            self.git(repository, "add", "README.md")
            self.git(repository, "commit", "-m", "later change")
            later_sha = self.git(repository, "rev-parse", "HEAD")
            with self.assertRaisesRegex(ConfigError, "does not resolve"):
                deployment_sha(
                    config,
                    "test",
                    later_sha,
                    repository,
                    previous_revision=release_sha,
                )

        self.assertEqual(pending_sha, release_sha)


if __name__ == "__main__":
    unittest.main()
