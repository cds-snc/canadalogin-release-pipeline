from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

from canadalogin_release.commands import CommandRunner


class CommandRunnerTest(unittest.TestCase):
    def test_suppressed_output_is_returned_but_not_logged(self) -> None:
        output = io.StringIO()
        error = io.StringIO()

        with redirect_stdout(output), redirect_stderr(error):
            result = CommandRunner().run(
                [sys.executable, "-c", "print('sensitive-task-definition')"],
                log_output=False,
            )

        self.assertEqual(result.stdout.strip(), "sensitive-task-definition")
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(error.getvalue(), "")

    def test_unset_environment_removes_parent_value_before_overrides(self) -> None:
        result = CommandRunner().run(
            [
                sys.executable,
                "-c",
                "import os; print(os.getenv('REMOVE_ME')); print(os.getenv('KEEP_ME'))",
            ],
            environment={"KEEP_ME": "configured"},
            unset_environment=("REMOVE_ME",),
            log_output=False,
        )

        self.assertEqual(result.stdout.splitlines(), ["None", "configured"])


if __name__ == "__main__":
    unittest.main()
