from __future__ import annotations

import json
import os
import subprocess
import sys

from acceptance.catalog import ROOT, SRC, AcceptanceTest


def run_verifier(
    test: AcceptanceTest,
    release_sha: str,
    pipeline_result: str,
    expected_result: str,
) -> int:
    environment = os.environ.copy()
    python_path = os.pathsep.join(
        value
        for value in (str(ROOT), str(SRC), environment.get("PYTHONPATH", ""))
        if value
    )
    environment.update(
        {
            "ACCEPTANCE_TEST_ID": test.test_id,
            "ACCEPTANCE_RESOURCES": json.dumps(test.resources, sort_keys=True),
            "ACCEPTANCE_ENVIRONMENT": str(test.release["environment"]),
            "RELEASE_SHA": release_sha,
            "PIPELINE_RESULT": pipeline_result,
            "EXPECTED_RESULT": expected_result,
            "PYTHONPATH": python_path,
        }
    )
    result = subprocess.run(
        [sys.executable, str(test.verify_script)],
        cwd=ROOT,
        env=environment,
        check=False,
    )
    return result.returncode
