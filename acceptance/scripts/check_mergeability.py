from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence

STATUS_CONTEXT = "Integration / acceptance tests"


class MergeabilityError(RuntimeError):
    pass


def check_current_commit(
    current_sha: str, tested_sha: str, statuses: Sequence[object]
) -> None:
    if not current_sha:
        raise MergeabilityError("The release pull request has no current commit SHA.")
    if tested_sha and tested_sha != current_sha:
        raise MergeabilityError(
            f"The current release commit {current_sha} differs from the tested "
            f"commit {tested_sha}. Comment !test to run integration and acceptance "
            "tests for the current commit."
        )

    matching_statuses = [
        status
        for status in statuses
        if isinstance(status, dict) and status.get("context") == STATUS_CONTEXT
    ]
    state = (
        matching_statuses[0].get("state", "missing") if matching_statuses else "missing"
    )
    if state == "success":
        return
    if state == "pending":
        raise MergeabilityError(
            f"Integration and acceptance tests are still running for current "
            f"commit {current_sha}. Wait for them to finish before merging."
        )
    raise MergeabilityError(
        f"Current release commit {current_sha} has not passed integration and "
        f"acceptance tests (status: {state}). Comment !test to test this exact "
        "commit before merging."
    )


def main() -> int:
    current_sha = os.environ.get("CURRENT_SHA", "")
    tested_sha = os.environ.get("TESTED_SHA", "")
    try:
        statuses = json.loads(os.environ.get("STATUSES_JSON", "[]"))
        if not isinstance(statuses, list):
            raise MergeabilityError("GitHub returned an invalid status response.")
        check_current_commit(current_sha, tested_sha, statuses)
    except (json.JSONDecodeError, MergeabilityError) as error:
        message = str(error)
        print(f"::error title=PR mergeability check::{message}")
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
