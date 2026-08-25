from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
USES_PATTERN = re.compile(r"^\s*uses:\s*([^\s#]+)", re.MULTILINE)
FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class WorkflowContractTest(unittest.TestCase):
    def workflow_files(self) -> list[Path]:
        return sorted((ROOT / ".github" / "workflows").glob("*.yml")) + [
            ROOT / "actions" / "setup" / "action.yml"
        ]

    def test_external_actions_are_pinned_to_full_commit_shas(self) -> None:
        failures = []
        for path in self.workflow_files():
            for reference in USES_PATTERN.findall(path.read_text()):
                if reference.startswith(("$/", "./")):
                    continue
                if "@" not in reference:
                    failures.append(f"{path}: {reference} has no ref")
                    continue
                ref = reference.rsplit("@", 1)[1]
                if not FULL_SHA_PATTERN.fullmatch(ref):
                    failures.append(f"{path}: {reference} is not pinned to a full SHA")
        self.assertEqual(failures, [])

    def test_privileged_pull_request_target_is_not_used(self) -> None:
        for path in self.workflow_files():
            with self.subTest(path=path.name):
                self.assertNotIn("pull_request_target", path.read_text())

    def test_deployment_requires_build_result_and_runs_sequentially(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()

        self.assertIn("needs: [plan, release_please, required_builds]", workflow)
        self.assertIn("needs.required_builds.result == 'success'", workflow)
        self.assertIn("max-parallel: 1", workflow)
        self.assertIn(
            "pipeline_id:\n        description: Optional concurrency namespace",
            workflow,
        )
        self.assertIn("default: default", workflow)
        self.assertIn(
            "group: canadalogin-release-${{ github.repository }}-${{ inputs.pipeline_id }}-",
            workflow,
        )
        self.assertIn("pipeline_id: ${{ inputs.pipeline_id }}", workflow)
        self.assertIn(
            "aws-region: ${{ inputs.aws-region || matrix.aws_region }}", workflow
        )

    def test_environment_deployments_share_the_pipeline_concurrency_namespace(
        self,
    ) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "deploy-environment.yml"
        ).read_text()

        self.assertIn(
            "pipeline_id:\n        description: Concurrency namespace", workflow
        )
        self.assertIn("default: default", workflow)
        self.assertIn(
            "group: canadalogin-release-${{ github.repository }}-${{ inputs.pipeline_id }}-${{ inputs.environment }}",
            workflow,
        )

    def test_workflow_checkouts_do_not_persist_credentials(self) -> None:
        for path in self.workflow_files():
            if path.suffix != ".yml":
                continue
            workflow = path.read_text()
            checkout_count = workflow.count("actions/checkout@")
            if checkout_count:
                with self.subTest(path=path.name):
                    self.assertEqual(
                        workflow.count("persist-credentials: false"), checkout_count
                    )

    def test_only_sbom_build_workflow_has_snapshot_write_permission(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()

        self.assertIn("contents: read\n      id-token: write", workflow)
        self.assertIn("sbom:\n", workflow)
        self.assertIn("sbom:\n    if: inputs.sbom-enabled", workflow)
        self.assertIn(
            "permissions:\n      contents: write\n      id-token: write", workflow
        )

    def test_acceptance_suite_is_explicitly_opt_in_and_fans_out(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "release-pipeline-tests.yml"
        ).read_text()

        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("\n  push:", workflow)
        self.assertNotIn("\n  pull_request:", workflow)
        self.assertIn("config-path: acceptance/scenarios/standard-ecs/", workflow)
        self.assertIn("config-path: acceptance/scenarios/react-ecs/", workflow)
        self.assertIn("config-path: acceptance/scenarios/failure-ecs/", workflow)
        self.assertEqual(workflow.count("pipeline_id: acceptance-"), 3)
        self.assertEqual(
            workflow.count(
                "    environment:\n"
                "      name: acceptance-tests\n"
                "      deployment: false\n"
            ),
            7,
        )
        self.assertEqual(
            workflow.count("      github-environment: acceptance-tests\n"), 3
        )
        self.assertEqual(workflow.count("      create-deployment: false\n"), 3)
        self.assertIn(
            "pipeline_id: acceptance-failure-${{ github.run_id }}\n"
            "      environment: acceptance-failure\n"
            "      github-environment: acceptance-tests\n"
            "      create-deployment: false\n"
            "      expect-health-check-failure: true\n"
            "      rebuild: true",
            workflow,
        )
        self.assertIn(
            "          EXPECTED_RESULT: success\n"
            "          GITHUB_REPOSITORY: ${{ github.repository }}\n"
            "          GH_TOKEN: ${{ github.token }}\n"
            "          PIPELINE_RESULT: ${{ needs.failure.result }}",
            workflow,
        )
        self.assertNotIn("\n    environment: acceptance-terraform\n", workflow)
        self.assertNotIn("\n    environment: acceptance-standard\n", workflow)
        self.assertNotIn("\n    environment: acceptance-react\n", workflow)
        self.assertNotIn("\n    environment: acceptance-failure\n", workflow)
        for scenario in ("standard-ecs", "react-ecs", "failure-ecs"):
            self.assertIn(
                "config-path: acceptance/scenarios/"
                f"{scenario}/release-pipeline-configuration.yml\n"
                "      aws-account-id: ${{ inputs.aws-account-id }}\n"
                "      aws-region: ${{ inputs.aws-region }}",
                workflow,
            )
        self.assertEqual(
            workflow.count(
                "        env:\n"
                "          AWS_ACCOUNT_ID: ${{ inputs.aws-account-id }}\n"
                "          AWS_REGION: ${{ inputs.aws-region }}\n"
            ),
            3,
        )
        self.assertIn(
            "needs: [prepare_standard, prepare_react, prepare_failure]", workflow
        )
        self.assertIn(
            "needs: [verify_standard, verify_react, verify_failure]", workflow
        )

        release_workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()
        self.assertEqual(
            release_workflow.count(
                "github-environment: ${{ inputs.github-environment || matrix.environment }}"
            ),
            3,
        )

        build_workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()
        self.assertIn(
            "name: ${{ inputs.github-environment || inputs.environment }}",
            build_workflow,
        )
        deploy_workflow = (
            ROOT / ".github" / "workflows" / "deploy-environment.yml"
        ).read_text()
        self.assertIn(
            "create-deployment:\n"
            "        description: Create a GitHub deployment record for the environment.\n"
            "        required: false\n"
            "        default: true\n"
            "        type: boolean",
            deploy_workflow,
        )
        self.assertIn(
            "environment:\n"
            "      name: ${{ inputs.github-environment || inputs.environment }}\n"
            "      deployment: ${{ inputs.create-deployment }}",
            deploy_workflow,
        )
        self.assertIn(
            "create-deployment: ${{ inputs.create-deployment }}", release_workflow
        )
        self.assertIn(
            "expect-health-check-failure:\n"
            "        description: Require the health check to fail and treat that failure as expected.\n"
            "        required: false\n"
            "        default: false\n"
            "        type: boolean",
            release_workflow,
        )
        self.assertIn(
            "expect-health-check-failure: ${{ inputs.expect-health-check-failure }}",
            release_workflow,
        )
        self.assertIn(
            "expect-health-check-failure:\n"
            "        description: Require the health check to fail and treat that failure as expected.\n"
            "        required: false\n"
            "        default: false\n"
            "        type: boolean",
            deploy_workflow,
        )
        self.assertIn(
            "id: health_check\n"
            "        continue-on-error: ${{ inputs.expect-health-check-failure }}",
            deploy_workflow,
        )
        self.assertIn(
            "HEALTH_CHECK_OUTCOME: ${{ steps.health_check.outcome }}",
            deploy_workflow,
        )
        self.assertIn("expected_outcome=failure", deploy_workflow)
        self.assertIn("expected_outcome=success", deploy_workflow)
        self.assertIn(
            "if: steps.validate_health_check.outcome == 'success' && steps.health_check.outcome == 'success'",
            deploy_workflow,
        )
        self.assertIn(
            "if: failure() || steps.health_check.outcome == 'failure' || steps.validate_health_check.outcome == 'failure'",
            deploy_workflow,
        )

    def test_acceptance_comment_dispatch_is_maintainer_gated(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "release-pipeline-test-command.yml"
        ).read_text()

        self.assertIn("issue_comment:", workflow)
        self.assertIn("types: [created]", workflow)
        self.assertIn('COMMENT_BODY" != "!test"', workflow)
        self.assertIn('head_repository" != "$REPOSITORY"', workflow)
        self.assertIn('base_branch" != "main"', workflow)
        self.assertIn("admin|maintain|push|write)", workflow)
        self.assertIn(
            'head_ref="$(jq -r \'.head.ref // ""\' <<<"$pr_json")"',
            workflow,
        )
        self.assertIn(
            "actions/workflows/release-pipeline-tests.yml/dispatches", workflow
        )
        self.assertIn('--arg ref "$head_ref"', workflow)
        self.assertIn('--arg sha "$head_sha"', workflow)
        self.assertIn(
            '{ref:$ref,inputs:{"release-sha":$sha,"pull-request-number":$pr}}', workflow
        )
        self.assertIn('--input - <<<"$payload"', workflow)
        self.assertNotIn('"inputs=$inputs"', workflow)
        self.assertNotIn("pull_request_target", workflow)

    def test_acceptance_comments_are_optional_after_status_updates(self) -> None:
        dispatcher = (
            ROOT / ".github" / "workflows" / "release-pipeline-test-command.yml"
        ).read_text()
        report = (
            ROOT / ".github" / "workflows" / "release-pipeline-tests.yml"
        ).read_text()

        for workflow in (dispatcher, report):
            self.assertIn("if ! gh api --method POST", workflow)
            self.assertIn("issues/$PR_NUMBER/comments", workflow)
            self.assertIn("could not be posted", workflow)

    def test_acceptance_preparation_waits_for_ecs_stability(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "release-pipeline-tests.yml"
        ).read_text()

        self.assertEqual(workflow.count("aws ecs wait services-stable"), 3)
        for scenario in ("standard", "react", "failure"):
            self.assertIn(f"--cluster cl-acceptance-{scenario}", workflow)
            self.assertIn(f"--services cl-acceptance-{scenario}-app", workflow)

    def test_release_please_is_standalone_and_uses_version_manifest(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release-please.yml").read_text()
        config = json.loads((ROOT / "release-please-config.json").read_text())
        manifest = json.loads((ROOT / ".release-please-manifest.json").read_text())

        self.assertIn("push:\n    branches:\n      - main", workflow)
        self.assertNotIn("workflow_call", workflow)
        self.assertIn("CDS_RELEASE_BOT_APP_ID", workflow)
        self.assertIn("CDS_RELEASE_BOT_PRIVATE_KEY", workflow)
        self.assertIn("config-file: release-please-config.json", workflow)
        self.assertIn("manifest-file: .release-please-manifest.json", workflow)
        self.assertEqual(config["release-type"], "simple")
        self.assertEqual(list(config["packages"]), ["."])
        self.assertEqual(list(manifest), ["."])
        self.assertRegex(manifest["."], r"^\d+\.\d+\.\d+$")
        self.assertEqual(
            config["extra-files"],
            [
                {
                    "type": "toml",
                    "path": "pyproject.toml",
                    "jsonpath": "$.project.version",
                }
            ],
        )

    def test_acceptance_requires_release_please_metadata_and_reports_status(
        self,
    ) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "release-pipeline-tests.yml"
        ).read_text()
        dispatcher = (
            ROOT / ".github" / "workflows" / "release-pipeline-test-command.yml"
        ).read_text()

        for candidate in (workflow, dispatcher):
            self.assertIn("release-please--branches--main", candidate)
            self.assertIn("autorelease: pending", candidate)
            self.assertIn('author_type" != "Bot"', candidate)
        self.assertIn("statuses: write", workflow)
        self.assertIn("context=release-pipeline-acceptance", workflow)
        self.assertIn("context=release-pipeline-acceptance", dispatcher)
        self.assertIn("required: true\n        type: string", workflow)
        self.assertIn("WORKFLOW_SHA", workflow)
        self.assertIn('$head_sha" != "$RELEASE_SHA"', workflow)

    def test_acceptance_gate_is_safe_and_requires_success_for_release_prs(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "release-pipeline-acceptance-gate.yml"
        ).read_text()

        self.assertIn("pull_request:", workflow)
        self.assertIn("status:", workflow)
        self.assertIn("statuses: read", workflow)
        self.assertIn("release-pipeline-acceptance", workflow)
        self.assertIn("acceptance_state", workflow)
        self.assertIn('acceptance_state" != success', workflow)
        self.assertNotIn("id-token:", workflow)
        self.assertNotIn("pull_request_target", workflow)


if __name__ == "__main__":
    unittest.main()
