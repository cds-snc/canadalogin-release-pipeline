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


if __name__ == "__main__":
    unittest.main()
