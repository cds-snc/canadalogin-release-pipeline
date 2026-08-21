from __future__ import annotations

import subprocess
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path

from canadalogin_release.config import HookConfig, PipelineConfig
from canadalogin_release.hooks import execute_hook
from canadalogin_release.runtime import RuntimeContext


class RecordingRunner:
    def __init__(self) -> None:
        self.unset_environment: tuple[str, ...] = ()
        self.environment: Mapping[str, str] = {}

    def run(
        self,
        arguments: Sequence[str],
        *,
        cwd: str | Path = ".",
        environment: Mapping[str, str] | None = None,
        unset_environment: Sequence[str] = (),
        check: bool = True,
        log_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        self.environment = environment or {}
        self.unset_environment = tuple(unset_environment)
        return subprocess.CompletedProcess(arguments, 0, "", "")


class HookTest(unittest.TestCase):
    def test_hooks_do_not_inherit_workflow_credentials(self) -> None:
        example = Path(__file__).parents[1] / "examples" / "gc-signin-static-website"
        config = PipelineConfig.load(example / "release-pipeline-configuration.yml")
        config = config.__class__(
            **{
                **config.__dict__,
                "hooks": HookConfig(before_deploy=(("python3", "hook.py"),)),
            }
        )
        runner = RecordingRunner()
        context = RuntimeContext.create(
            repository=example,
            environment="dev",
            sha="abc123",
            release_tag=None,
            github_ref="refs/heads/main",
            variables={"EXAMPLE_VARIABLE": "value"},
        )

        execute_hook(config, "before-deploy", context, runner=runner)

        self.assertIn("AWS_ACCESS_KEY_ID", runner.unset_environment)
        self.assertIn("AWS_WEB_IDENTITY_TOKEN_FILE", runner.unset_environment)
        self.assertIn("GITHUB_TOKEN", runner.unset_environment)
        self.assertEqual(runner.environment["EXAMPLE_VARIABLE"], "value")


if __name__ == "__main__":
    unittest.main()
