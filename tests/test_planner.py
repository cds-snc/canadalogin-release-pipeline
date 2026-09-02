from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from canadalogin_release.config import ConfigError, PipelineConfig
from canadalogin_release.planner import create_plan

EXAMPLES = Path(__file__).parents[1] / "examples"


class PlannerTest(unittest.TestCase):
    def config(self, repository: str) -> PipelineConfig:
        return PipelineConfig.load(
            EXAMPLES / repository / "release-pipeline-configuration.yml"
        )

    def repository_with_version(
        self, environment: str, version: str
    ) -> tempfile.TemporaryDirectory:
        directory = tempfile.TemporaryDirectory()
        version_directory = Path(directory.name) / ".deployed_versions"
        version_directory.mkdir()
        (version_directory / f"{environment}.json").write_text(
            json.dumps({"version": version})
        )
        return directory

    @staticmethod
    def resolve_sha(
        config: PipelineConfig,
        environment: str,
        workflow_sha: str,
        repository: str | Path,
    ) -> str:
        if environment == config.environments.development:
            return workflow_sha
        return f"{environment}-sha"

    def test_push_builds_every_artifact_and_deploys_promoted_environments(self) -> None:
        config = self.config("canadalogin-user-selfservice-webapp")
        with self.repository_with_version("test", "1.2.3") as repository:
            plan = create_plan(
                config,
                event_name="push",
                sha="abc123",
                repository=repository,
                changed_paths=[".deployed_versions/test.json"],
                sha_resolver=self.resolve_sha,
            )

        self.assertTrue(plan.release_please)
        self.assertEqual([item.environment for item in plan.promotions], ["test"])
        self.assertEqual(len(plan.required_builds), 6)
        self.assertEqual(plan.target_environments, ("dev", "test"))
        self.assertEqual(plan.required_builds[-1]["name"], "load-test")
        self.assertEqual(plan.required_builds[-1]["sha"], "staging-sha")
        self.assertEqual(
            [deployment["sha"] for deployment in plan.deployments],
            ["abc123", "test-sha"],
        )

    def test_push_deploys_only_dev_without_version_promotions(self) -> None:
        config = self.config("canadalogin-user-selfservice-webapp")
        plan = create_plan(
            config,
            event_name="push",
            sha="abc123",
            changed_paths=[],
            sha_resolver=self.resolve_sha,
        )

        self.assertEqual(plan.target_environments, ("dev",))

    def test_manual_dev_rebuild_still_refreshes_staging_load_test(self) -> None:
        config = self.config("canadalogin-user-selfservice-webapp")
        plan = create_plan(
            config,
            event_name="workflow_dispatch",
            manual_environment="dev",
            rebuild=True,
            sha="main-sha",
            changed_paths=[],
            sha_resolver=self.resolve_sha,
        )

        load_test = next(
            build for build in plan.required_builds if build["name"] == "load-test"
        )
        self.assertEqual(load_test["sha"], "staging-sha")

    def test_manual_all_deduplicates_shared_artifacts_by_source_sha(self) -> None:
        config = self.config("canadalogin-user-selfservice-webapp")

        def resolve_with_current_test(
            config: PipelineConfig,
            environment: str,
            workflow_sha: str,
            repository: str | Path,
        ) -> str:
            if environment in {"dev", "test"}:
                return workflow_sha
            return f"{environment}-sha"

        plan = create_plan(
            config,
            event_name="workflow_dispatch",
            manual_environment="all",
            rebuild=True,
            sha="main-sha",
            changed_paths=[],
            sha_resolver=resolve_with_current_test,
        )
        backend_builds = [
            build for build in plan.required_builds if build["name"] == "backend"
        ]

        self.assertEqual(
            [build["sha"] for build in backend_builds],
            ["main-sha", "staging-sha", "prod-sha"],
        )
        self.assertEqual(
            [build["sbom_enabled"] for build in backend_builds],
            [True, False, False],
        )

    def test_pull_request_only_reports_promotions(self) -> None:
        config = self.config("canadalogin-user-selfservice-webapp")
        with self.repository_with_version("prod", "1.2.3") as repository:
            plan = create_plan(
                config,
                event_name="pull_request",
                sha="abc123",
                repository=repository,
                changed_paths=[".deployed_versions/prod.json"],
            )

        self.assertEqual([item.environment for item in plan.promotions], ["prod"])
        self.assertFalse(plan.required_builds)
        self.assertFalse(plan.deployments)
        self.assertFalse(plan.release_please)

    def test_empty_work_matrices_have_disabled_sentinels(self) -> None:
        config = self.config("canadalogin-user-selfservice-webapp")
        plan = create_plan(
            config,
            event_name="pull_request",
            sha="abc123",
            changed_paths=[],
        )

        for output_name in (
            "required_build_matrix",
            "deployment_matrix",
        ):
            with self.subTest(output_name=output_name):
                entry = json.loads(plan.github_outputs()[output_name])["include"][0]
                self.assertFalse(entry["enabled"])

    def test_repository_dispatch_rebuilds_and_deploys_only_configured_environment(
        self,
    ) -> None:
        config = self.config("canadalogin-static-website")
        plan = create_plan(
            config,
            event_name="repository_dispatch",
            repository_dispatch_event="gc-articles-update",
            sha="abc123",
            changed_paths=[],
            sha_resolver=self.resolve_sha,
        )

        self.assertEqual(
            [item["environment"] for item in plan.required_builds], ["dev"]
        )
        self.assertEqual(plan.target_environments, ("dev",))

    def test_rejects_unconfigured_repository_dispatch_event(self) -> None:
        config = self.config("canadalogin-static-website")

        with self.assertRaisesRegex(ConfigError, "not configured"):
            create_plan(
                config,
                event_name="repository_dispatch",
                repository_dispatch_event="typo",
                sha="abc123",
                changed_paths=[],
            )

    def test_force_deploy_selects_all_environments(self) -> None:
        config = self.config("canadalogin-user-selfservice-webapp")
        plan = create_plan(
            config,
            event_name="workflow_dispatch",
            manual_environment="staging",
            force_deploy=True,
            rebuild=False,
            sha="abc123",
            changed_paths=[],
            sha_resolver=self.resolve_sha,
        )

        self.assertEqual(
            [build["name"] for build in plan.required_builds], ["load-test"]
        )
        self.assertEqual(plan.required_builds[0]["sha"], "staging-sha")
        self.assertEqual(plan.target_environments, ("dev", "test", "staging", "prod"))
        self.assertTrue(all(deployment["notify"] for deployment in plan.deployments))

    def test_manual_dev_run_still_refreshes_staging_load_test(self) -> None:
        config = self.config("canadalogin-user-selfservice-webapp")
        plan = create_plan(
            config,
            event_name="workflow_dispatch",
            manual_environment="dev",
            rebuild=False,
            sha="main-sha",
            changed_paths=[],
            sha_resolver=self.resolve_sha,
        )

        self.assertEqual(
            [
                (
                    build["name"],
                    build["environment"],
                    build["target_environment"],
                    build["sha"],
                )
                for build in plan.required_builds
            ],
            [("load-test", "staging", "staging", "staging-sha")],
        )

    def test_manual_staging_rebuild_uses_staging_desired_sha(self) -> None:
        config = self.config("canadalogin-user-selfservice-webapp")
        plan = create_plan(
            config,
            event_name="workflow_dispatch",
            manual_environment="staging",
            rebuild=True,
            sha="main-sha",
            changed_paths=[],
            sha_resolver=self.resolve_sha,
        )

        self.assertEqual(
            [
                (
                    build["name"],
                    build["environment"],
                    build["target_environment"],
                    build["sha"],
                )
                for build in plan.required_builds
            ],
            [
                ("frontend", "staging", "staging", "staging-sha"),
                ("backend", "dev", "staging", "staging-sha"),
                ("load-test", "staging", "staging", "staging-sha"),
            ],
        )
        backend = next(
            build for build in plan.required_builds if build["name"] == "backend"
        )
        self.assertFalse(backend["sbom_enabled"])

    def test_rejects_disabled_manual_environment(self) -> None:
        config = self.config("canadalogin-user-selfservice-webapp")
        with self.assertRaisesRegex(ConfigError, "Unknown or disabled"):
            create_plan(
                config,
                event_name="workflow_dispatch",
                manual_environment="qa",
                sha="abc123",
                changed_paths=[],
            )


if __name__ == "__main__":
    unittest.main()
