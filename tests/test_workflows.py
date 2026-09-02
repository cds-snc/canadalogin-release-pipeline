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

    def test_unit_test_check_has_an_explicit_user_facing_name(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "unit-tests.yml").read_text()

        self.assertIn("name: Unit tests\n", workflow)
        self.assertIn("  test:\n    name: Unit tests", workflow)
        self.assertNotIn("name: CI\n", workflow)

    def test_privileged_pull_request_target_is_not_used(self) -> None:
        for path in self.workflow_files():
            with self.subTest(path=path.name):
                self.assertNotIn("pull_request_target", path.read_text())

    def test_deployment_requires_build_result_and_runs_independently(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "release-system.yml"
        ).read_text()
        pipeline = (
            ROOT
            / ".github"
            / "workflows"
            / "internal-release-system-interface.yml"
        ).read_text()
        deployment = pipeline.split("\n  deploy:\n", 1)[1]

        self.assertIn("name: CanadaLogin release system\n", workflow)
        self.assertIn(
            "name: CanadaLogin internal release system interface\n", pipeline
        )
        self.assertIn(
            "description: Force deployments to all enabled environments.", workflow
        )
        self.assertIn("needs: [plan, release_please, required_builds]", pipeline)
        self.assertIn("needs.required_builds.result == 'success'", pipeline)
        self.assertIn("required-builds-result:", pipeline)
        self.assertIn("deploy-result:", pipeline)
        self.assertIn("strategy:\n      fail-fast: false\n      matrix:", deployment)
        self.assertNotIn("max-parallel: 1", deployment)
        self.assertNotIn("aws-account-id:", workflow)
        self.assertNotIn("aws-region:", workflow)
        self.assertNotIn("github-environment:", workflow)
        self.assertNotIn("create-deployment:", workflow)
        self.assertNotIn("expect-health-check-failure:", workflow)
        self.assertNotIn("pipeline_id:", workflow)
        self.assertNotIn("release-pipeline-vars:", workflow)
        self.assertIn(
            "group: canadalogin-release-${{ github.repository }}-${{ inputs.pipeline_id }}-",
            pipeline,
        )
        acceptance_test = (
            ROOT / ".github" / "workflows" / "run-one-acceptance-test.yml"
        ).read_text()
        self.assertFalse(
            (ROOT / ".github" / "workflows" / "acceptance-release.yml").exists()
        )
        self.assertIn(
            "uses: ./.github/workflows/internal-release-system-interface.yml",
            acceptance_test,
        )
        self.assertIn("pipeline_id: ${{ inputs.pipeline-id-prefix }}-${{ github.run_id }}", acceptance_test)
        self.assertIn(
            "aws-region: ${{ inputs.aws-region || matrix.aws_region }}", pipeline
        )
        self.assertIn(
            "RELEASE_FORCE_DEPLOY: ${{ inputs.force-redeploy }}", pipeline
        )
        self.assertNotIn(
            "force-redeploy: ${{ fromJSON(needs.plan.outputs.", deployment
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

    def test_deployment_workflow_has_no_lifecycle_hook_steps(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "deploy-environment.yml"
        ).read_text()

        self.assertNotIn("canadalogin-release hook", workflow)
        self.assertNotIn("expect-health-check-failure", workflow)

    def test_plan_workflow_formats_build_and_deployment_matrices(self) -> None:
        workflow = (
            ROOT
            / ".github"
            / "workflows"
            / "internal-release-system-interface.yml"
        ).read_text()

        self.assertIn('plan_outputs="$(mktemp)"', workflow)
        self.assertIn('GITHUB_OUTPUT="$plan_outputs" canadalogin-release plan', workflow)
        self.assertIn('cat "$plan_outputs" >> "$GITHUB_OUTPUT"', workflow)
        self.assertNotIn('plan="$(canadalogin-release plan', workflow)
        self.assertIn("printf 'Required builds:\\n'", workflow)
        self.assertIn(
            "sed -n 's/^required_build_matrix=//p' \"$plan_outputs\" | jq .",
            workflow,
        )
        self.assertIn("printf '\\nDeployments:\\n'", workflow)
        self.assertIn(
            "sed -n 's/^deployment_matrix=//p' \"$plan_outputs\" | jq .",
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

    def test_acceptance_jobs_use_environments_without_deployments(self) -> None:
        acceptance = (ROOT / ".github" / "workflows" / "acceptance-tests.yml").read_text()
        acceptance_test = (
            ROOT / ".github" / "workflows" / "run-one-acceptance-test.yml"
        ).read_text()
        build = (ROOT / ".github" / "workflows" / "build.yml").read_text()
        deploy = (ROOT / ".github" / "workflows" / "deploy-environment.yml").read_text()

        self.assertIn("name: acceptance-tests\n      deployment: false", acceptance)
        self.assertEqual(acceptance_test.count("deployment: false"), 2)
        self.assertEqual(build.count("deployment: false"), 2)
        self.assertIn(
            "deployment: ${{ inputs.github-environment != 'acceptance-tests' }}",
            deploy,
        )

    def test_only_sbom_build_workflow_has_snapshot_write_permission(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()

        self.assertIn("contents: read\n      id-token: write", workflow)
        self.assertIn("sbom:\n", workflow)
        self.assertIn("sbom:\n    if: inputs.sbom-enabled", workflow)
        self.assertIn(
            "permissions:\n      contents: write\n      id-token: write", workflow
        )

    def test_unit_tests_lint_workflows(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "unit-tests.yml").read_text()

        self.assertIn(
            "go install github.com/rhysd/actionlint/cmd/actionlint@v1.7.12", workflow
        )
        self.assertIn('"$(go env GOPATH)/bin/actionlint" .github/workflows/*.yml', workflow)

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

    def test_acceptance_failure_fixtures_do_not_tolerate_reusable_job_failures(
        self,
    ) -> None:
        acceptance_test = (
            ROOT / ".github" / "workflows" / "run-one-acceptance-test.yml"
        ).read_text()
        pipeline = (
            ROOT / ".github" / "workflows" / "internal-release-system-interface.yml"
        ).read_text()
        build = (ROOT / ".github" / "workflows" / "build.yml").read_text()
        deployment = (
            ROOT / ".github" / "workflows" / "deploy-environment.yml"
        ).read_text()

        release = acceptance_test.split("\n  release:\n", 1)[1].split(
            "\n  verify:\n", 1
        )[0]
        self.assertNotIn("continue-on-error", release)
        self.assertIn("allow-failure: ${{ inputs.expected-release-result == 'failure' }}", release)
        self.assertIn("allow-failure:", pipeline)
        self.assertIn("continue-on-error: ${{ inputs.allow-failure }}", build)
        self.assertIn("continue-on-error: ${{ inputs.allow-failure }}", deployment)
        self.assertIn("needs.required_builds.outputs.result", pipeline)
        self.assertIn("needs.deploy.outputs.result", pipeline)

    def test_acceptance_github_orchestration_uses_python_commands(self) -> None:
        command = (
            ROOT / ".github" / "workflows" / "release-pipeline-test-command.yml"
        ).read_text()
        suite = (
            ROOT / ".github" / "workflows" / "acceptance-tests.yml"
        ).read_text()

        self.assertIn("uses: ./actions/setup", command)
        self.assertIn("acceptance/runner.py request", command)
        self.assertNotIn("gh api", command)
        self.assertNotIn("jq ", command)
        self.assertIn("acceptance/runner.py validate-request", suite)
        self.assertIn("acceptance/runner.py report", suite)
        self.assertNotIn("assert_suite:", suite)
        self.assertIn("needs: [validate_request, terraform, acceptance]", suite)

if __name__ == "__main__":
    unittest.main()
