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
from acceptance.github import (
    GitHubError,
    report_acceptance_result,
    request_acceptance_tests,
    validate_acceptance_request,
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

    request_parser = subparsers.add_parser(
        "request", help="Validate a test command and dispatch the acceptance suite"
    )
    request_parser.add_argument("--comment-body", required=True)
    request_parser.add_argument("--commenter", required=True)
    request_parser.add_argument("--pull-request", required=True, type=int)
    request_parser.add_argument("--repository", required=True)
    request_parser.add_argument("--server-url", required=True)
    request_parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))

    request_validation_parser = subparsers.add_parser(
        "validate-request", help="Validate a dispatched acceptance test request"
    )
    request_validation_parser.add_argument("--pull-request", required=True, type=int)
    request_validation_parser.add_argument("--release-sha", required=True)
    request_validation_parser.add_argument("--repository", required=True)
    request_validation_parser.add_argument("--workflow-ref", required=True)
    request_validation_parser.add_argument("--workflow-sha", required=True)
    request_validation_parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))

    report_parser = subparsers.add_parser(
        "report", help="Publish the acceptance suite status and pull request comment"
    )
    report_parser.add_argument("--pull-request", required=True, type=int)
    report_parser.add_argument("--release-sha", required=True)
    report_parser.add_argument("--repository", required=True)
    report_parser.add_argument("--run-url", required=True)
    report_parser.add_argument("--validation-result", required=True)
    report_parser.add_argument("--terraform-result", required=True)
    report_parser.add_argument("--acceptance-result", required=True)
    report_parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
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
        if options.command == "request":
            request_acceptance_tests(
                comment_body=options.comment_body,
                commenter=options.commenter,
                pull_request=options.pull_request,
                repository=options.repository,
                server_url=options.server_url,
                token=os.environ.get("GITHUB_TOKEN", ""),
                api_url=options.api_url,
            )
            return 0
        if options.command == "validate-request":
            validate_acceptance_request(
                pull_request=options.pull_request,
                release_sha=options.release_sha,
                repository=options.repository,
                workflow_ref=options.workflow_ref,
                workflow_sha=options.workflow_sha,
                token=os.environ.get("GITHUB_TOKEN", ""),
                api_url=options.api_url,
            )
            return 0
        if options.command == "report":
            report_acceptance_result(
                pull_request=options.pull_request,
                release_sha=options.release_sha,
                repository=options.repository,
                run_url=options.run_url,
                validation_result=options.validation_result,
                terraform_result=options.terraform_result,
                acceptance_result=options.acceptance_result,
                token=os.environ.get("GITHUB_TOKEN", ""),
                api_url=options.api_url,
            )
            return 0
    except (CatalogError, GitHubError, OSError) as error:
        parser.exit(2, f"error: {error}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
