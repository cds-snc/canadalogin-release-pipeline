from __future__ import annotations

import importlib.util
import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

POLICY_PATH = (
    Path(__file__).parents[1] / "acceptance" / "scripts" / "check_mergeability.py"
)
POLICY_SPEC = importlib.util.spec_from_file_location("check_mergeability", POLICY_PATH)
if POLICY_SPEC is None or POLICY_SPEC.loader is None:
    raise RuntimeError(f"Unable to load {POLICY_PATH}")
POLICY = importlib.util.module_from_spec(POLICY_SPEC)
POLICY_SPEC.loader.exec_module(POLICY)


class MergeabilityTest(unittest.TestCase):
    def test_missing_status_emits_a_visible_error_annotation(self) -> None:
        output = io.StringIO()
        error = io.StringIO()

        with (
            patch.dict(
                os.environ,
                {
                    "CURRENT_SHA": "a" * 40,
                    "TESTED_SHA": "",
                    "STATUSES_JSON": "[]",
                },
            ),
            redirect_stdout(output),
            redirect_stderr(error),
        ):
            result = POLICY.main()

        self.assertEqual(result, 1)
        self.assertIn("::error title=PR mergeability check::", output.getvalue())
        self.assertIn("Comment !test", error.getvalue())

    def test_current_commit_with_latest_successful_status_is_mergeable(self) -> None:
        POLICY.check_current_commit(
            "a" * 40,
            "a" * 40,
            [
                {
                    "context": "Integration / acceptance tests",
                    "state": "success",
                }
            ],
        )

    def test_new_commit_cannot_reuse_status_from_previous_test_event(self) -> None:
        with self.assertRaisesRegex(
            POLICY.MergeabilityError, "differs from the tested"
        ):
            POLICY.check_current_commit("b" * 40, "a" * 40, [])

    def test_missing_current_commit_status_requires_test_command(self) -> None:
        with self.assertRaisesRegex(POLICY.MergeabilityError, "Comment !test"):
            POLICY.check_current_commit("a" * 40, "", [])

    def test_pending_current_commit_status_waits_without_retesting(self) -> None:
        with self.assertRaisesRegex(POLICY.MergeabilityError, "still running"):
            POLICY.check_current_commit(
                "a" * 40,
                "",
                [
                    {
                        "context": "Integration / acceptance tests",
                        "state": "pending",
                    }
                ],
            )

    def test_latest_status_for_context_is_authoritative(self) -> None:
        with self.assertRaisesRegex(POLICY.MergeabilityError, "status: failure"):
            POLICY.check_current_commit(
                "a" * 40,
                "a" * 40,
                [
                    {
                        "context": "Integration / acceptance tests",
                        "state": "failure",
                    },
                    {
                        "context": "Integration / acceptance tests",
                        "state": "success",
                    },
                ],
            )


if __name__ == "__main__":
    unittest.main()
