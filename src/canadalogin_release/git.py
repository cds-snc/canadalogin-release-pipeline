from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .config import NOTIFICATION_WORKFLOW_SECRETS


class GitError(RuntimeError):
    """Raised when a required Git operation fails."""


def run_git(
    arguments: Sequence[str], repository: str | Path = ".", *, required: bool = True
) -> str:
    environment = os.environ.copy()
    for name in NOTIFICATION_WORKFLOW_SECRETS:
        environment.pop(name, None)
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if required and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise GitError(f"git {' '.join(arguments)} failed: {detail}")
    return result.stdout.strip() if result.returncode == 0 else ""


def changed_paths(
    repository: str | Path,
    before_sha: str,
    sha: str,
    path: str | Path,
    *,
    three_dot: bool = False,
) -> tuple[str, ...]:
    if not before_sha or set(before_sha) == {"0"}:
        before_sha = run_git(["rev-parse", f"{sha}^"], repository, required=False)
    if not before_sha:
        return ()
    revisions = [f"{before_sha}...{sha}"] if three_dot else [before_sha, sha]
    output = run_git(
        [
            "diff",
            "--name-only",
            "--diff-filter=ACMRT",
            *revisions,
            "--",
            str(path),
        ],
        repository,
    )
    return tuple(line for line in output.splitlines() if line)


def file_at_revision(
    repository: str | Path, revision: str, path: str | Path
) -> str | None:
    if not revision or set(revision) == {"0"}:
        return None
    value = run_git(["show", f"{revision}:{path}"], repository, required=False)
    return value or None
