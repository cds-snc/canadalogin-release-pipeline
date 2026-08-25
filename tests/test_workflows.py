from __future__ import annotations

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
        self.assertIn(
            "permissions:\n"
            "  actions: read\n"
            "  contents: write\n"
            "  id-token: write\n"
            "  issues: write\n"
            "  pull-requests: write\n",
            workflow,
        )
        self.assertIn(
            "TF_VAR_github_oidc_subject_prefix: repo:${{ github.repository_owner }}@"
            "${{ github.repository_owner_id }}/${{ github.event.repository.name }}@"
            "${{ github.repository_id }}",
            workflow,
        )
        self.assertIn(
            "  report:\n"
            "    name: Report acceptance suite result\n"
            "    if: always()\n"
            "    needs: [assert_suite]\n"
            "    runs-on: ubuntu-latest\n"
            "    permissions:\n"
            "      issues: write\n"
            "      pull-requests: write\n",
            workflow,
        )
        self.assertIn("config-path: acceptance/scenarios/standard-ecs/", workflow)
        self.assertIn("config-path: acceptance/scenarios/react-ecs/", workflow)
        self.assertIn("config-path: acceptance/scenarios/failure-ecs/", workflow)
        self.assertEqual(workflow.count("pipeline_id: acceptance-"), 3)
        self.assertIn("    environment: acceptance-tests\n", workflow)
        self.assertEqual(
            workflow.count("      github-environment: acceptance-tests\n"), 3
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
            "environment: ${{ inputs.github-environment || inputs.environment }}",
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
        self.assertIn("admin|maintain|push)", workflow)
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


if __name__ == "__main__":
    unittest.main()
