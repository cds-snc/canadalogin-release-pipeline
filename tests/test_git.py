from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from canadalogin_release.git import changed_paths, run_git


class GitTest(unittest.TestCase):
    def git(self, repository: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_pull_request_diff_excludes_changes_added_only_to_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            self.git(repository, "init", "-b", "main")
            self.git(repository, "config", "user.name", "Release Test")
            self.git(repository, "config", "user.email", "release@example.invalid")
            versions = repository / ".deployed_versions"
            versions.mkdir()
            (versions / "test.json").write_text(json.dumps({"version": "1.0.0"}))
            (repository / "README.md").write_text("base\n")
            self.git(repository, "add", ".")
            self.git(repository, "commit", "-m", "base")

            self.git(repository, "checkout", "-b", "feature")
            (repository / "README.md").write_text("feature\n")
            self.git(repository, "commit", "-am", "feature")
            feature_sha = self.git(repository, "rev-parse", "HEAD")

            self.git(repository, "checkout", "main")
            (versions / "test.json").write_text(json.dumps({"version": "1.1.0"}))
            self.git(repository, "commit", "-am", "base promotion")
            main_sha = self.git(repository, "rev-parse", "HEAD")

            pull_request_paths = changed_paths(
                repository,
                main_sha,
                feature_sha,
                ".deployed_versions",
                three_dot=True,
            )
            two_dot_paths = changed_paths(
                repository, main_sha, feature_sha, ".deployed_versions"
            )

        self.assertEqual(pull_request_paths, ())
        self.assertEqual(two_dot_paths, (".deployed_versions/test.json",))

    def test_git_checkout_hook_does_not_inherit_build_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            self.git(repository, "init", "-b", "main")
            self.git(repository, "config", "user.name", "Release Test")
            self.git(repository, "config", "user.email", "release@example.invalid")
            (repository / "README.md").write_text("base\n")
            self.git(repository, "add", ".")
            self.git(repository, "commit", "-m", "base")
            captured = repository / "captured-secret.txt"
            hook = repository / ".git" / "hooks" / "post-checkout"
            hook.write_text(
                f"#!/bin/sh\nprintf '%s' \"${{BUILD_SECRET_1-unset}}\" > {captured}\n"
            )
            hook.chmod(0o755)

            with patch.dict(os.environ, {"BUILD_SECRET_1": "sensitive"}):
                run_git(["checkout", "-b", "other"], repository)

            value = captured.read_text()

        self.assertEqual(value, "unset")


if __name__ == "__main__":
    unittest.main()
