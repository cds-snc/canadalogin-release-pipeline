from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

AWS_CREDENTIALS = frozenset(
    {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_ROLE_ARN",
        "AWS_PROFILE",
        "AWS_DEFAULT_PROFILE",
        "AWS_CONFIG_FILE",
        "AWS_SHARED_CREDENTIALS_FILE",
    }
)
GITHUB_CREDENTIALS = frozenset(
    {
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "GITHUB_APP_TOKEN",
        "ACTIONS_RUNTIME_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_URL",
    }
)
WORKFLOW_CREDENTIALS = frozenset({*AWS_CREDENTIALS, *GITHUB_CREDENTIALS})


class CommandError(RuntimeError):
    """Raised when an external command fails."""


class CommandRunner:
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
        if not arguments or any(
            not isinstance(argument, str) for argument in arguments
        ):
            raise CommandError("Commands must contain one or more string arguments")
        merged_environment = os.environ.copy()
        for name in unset_environment:
            merged_environment.pop(name, None)
        if environment:
            merged_environment.update(environment)
        result = subprocess.run(
            list(arguments),
            cwd=cwd,
            env=merged_environment,
            check=False,
            text=True,
            capture_output=True,
        )
        if log_output and result.stdout:
            print(result.stdout, end="")
        if log_output and result.stderr:
            print(result.stderr, end="", file=os.sys.stderr)
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            detail_suffix = f": {detail}" if detail else ""
            raise CommandError(
                f"Command failed with exit code {result.returncode}: "
                + " ".join(arguments)
                + detail_suffix
            )
        return result
