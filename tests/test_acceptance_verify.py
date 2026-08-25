from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

VERIFY_PATH = Path(__file__).parents[1] / "acceptance" / "scripts" / "verify.py"
VERIFY_SPEC = importlib.util.spec_from_file_location("acceptance_verify", VERIFY_PATH)
if VERIFY_SPEC is None or VERIFY_SPEC.loader is None:
    raise RuntimeError(f"Unable to load {VERIFY_PATH}")
VERIFY = importlib.util.module_from_spec(VERIFY_SPEC)
VERIFY_SPEC.loader.exec_module(VERIFY)


class AcceptanceVerifyTest(unittest.TestCase):
    def test_scenario_uses_full_ecr_uri_for_live_identity_checks(self) -> None:
        scenario = VERIFY.scenario_for_account(
            VERIFY.SCENARIOS["standard-ecs"],
            "123456789012",
            "us-east-1",
        )

        self.assertEqual(
            scenario["ecr_uri"],
            "123456789012.dkr.ecr.us-east-1.amazonaws.com/cl-acceptance-standard",
        )


if __name__ == "__main__":
    unittest.main()
