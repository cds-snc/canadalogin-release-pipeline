from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from acceptance.catalog import (
    CatalogError,
    discover,
    select_tests,
    validate_catalog,
)
from acceptance.verify import run_verifier


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="acceptance-runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List discovered acceptance tests")

    validate_parser = subparsers.add_parser(
        "validate", help="Validate test manifests and release configs"
    )
    validate_parser.add_argument("--test", dest="test_id")
    validate_parser.add_argument("--all", action="store_true")

    matrix_parser = subparsers.add_parser(
        "matrix", help="Emit the enabled GitHub Actions matrix"
    )
    matrix_parser.add_argument("--github-output", action="store_true")
    matrix_parser.add_argument(
        "--aws-account-id", default=os.environ.get("AWS_ACCOUNT_ID", "429694360874")
    )
    matrix_parser.add_argument(
        "--aws-region", default=os.environ.get("AWS_REGION", "ca-central-1")
    )

    verify_parser = subparsers.add_parser(
        "verify", help="Run one test-owned live verifier"
    )
    verify_parser.add_argument("--test", dest="test_id", required=True)
    verify_parser.add_argument("--sha", default=os.environ.get("RELEASE_SHA", ""))
    verify_parser.add_argument(
        "--pipeline-result", default=os.environ.get("PIPELINE_RESULT", "")
    )
    verify_parser.add_argument(
        "--expected-result", default=os.environ.get("EXPECTED_RESULT", "success")
    )
    return parser


def main(arguments: list[str] | None = None) -> int:
    parser = build_parser()
    options = parser.parse_args(arguments)
    try:
        tests = discover()
        if options.command == "list":
            for test in tests:
                state = "enabled" if test.enabled else "disabled"
                print(f"{test.test_id}\t{state}\t{test.name}")
            return 0

        if options.command == "validate":
            validate_catalog(tests)
            selected = select_tests(tests, options.test_id)
            for test in selected:
                print(f"Validated {test.test_id}: {test.config_path.relative_to(ROOT)}")
            return 0

        if options.command == "matrix":
            validate_catalog(tests)
            matrix = [
                test.matrix_item(options.aws_account_id, options.aws_region)
                for test in select_tests(tests, None)
            ]
            encoded = json.dumps(matrix, separators=(",", ":"), sort_keys=True)
            if options.github_output:
                output_path = os.environ.get("GITHUB_OUTPUT")
                if not output_path:
                    raise CatalogError("GITHUB_OUTPUT is required with --github-output")
                with open(output_path, "a", encoding="utf-8") as output:
                    output.write(f"matrix={encoded}\n")
            print(encoded)
            return 0

        if options.command == "verify":
            validate_catalog(tests)
            test = select_tests(tests, options.test_id)[0]
            return run_verifier(
                test,
                options.sha,
                options.pipeline_result,
                options.expected_result,
            )
    except (CatalogError, OSError) as error:
        parser.exit(2, f"error: {error}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
