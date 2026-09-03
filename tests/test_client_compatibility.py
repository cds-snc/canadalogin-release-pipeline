from __future__ import annotations

import unittest
from pathlib import Path

from canadalogin_release.build import execute_build as legacy_execute_build
from canadalogin_release.config import PipelineConfig
from canadalogin_release.pipeline.build import execute_build
from canadalogin_release.pipeline.planner import create_plan
from canadalogin_release.planner import create_plan as legacy_create_plan

EXAMPLES = Path(__file__).parents[1] / "examples"
CLIENT_SHAPES = {
    "canadalogin-user-selfservice-webapp": {
        "builds": ("frontend", "backend", "load-test"),
        "deployment_kinds": ("s3", "ecs"),
        "ecs_services": 1,
        "s3_targets": 1,
        "cloudfront_invalidations": 1,
    },
    "canadalogin-static-website": {
        "builds": ("website",),
        "deployment_kinds": ("s3",),
        "ecs_services": 0,
        "s3_targets": 2,
        "cloudfront_invalidations": 2,
    },
}


class ClientCompatibilityTest(unittest.TestCase):
    def config(self, repository: str) -> PipelineConfig:
        return PipelineConfig.load(
            EXAMPLES / repository / "release-pipeline-configuration.yml"
        )

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

    def test_client_examples_preserve_their_deployment_shapes(self) -> None:
        for repository, expected in CLIENT_SHAPES.items():
            with self.subTest(repository=repository):
                config = self.config(repository)
                self.assertEqual(
                    tuple(build.name for build in config.builds), expected["builds"]
                )
                self.assertEqual(
                    tuple(deployment.kind for deployment in config.deployments),
                    expected["deployment_kinds"],
                )
                self.assertEqual(
                    sum(
                        len(deployment.services)
                        for deployment in config.deployments
                        if deployment.kind == "ecs"
                    ),
                    expected["ecs_services"],
                )
                self.assertEqual(
                    sum(
                        len(deployment.targets)
                        for deployment in config.deployments
                        if deployment.kind == "s3"
                    ),
                    expected["s3_targets"],
                )
                self.assertEqual(
                    sum(
                        len(deployment.invalidations)
                        for deployment in config.deployments
                        if deployment.kind == "s3"
                    ),
                    expected["cloudfront_invalidations"],
                )

    def test_push_plans_build_every_artifact_and_deploy_dev(self) -> None:
        workflow_sha = "a" * 40
        for repository in CLIENT_SHAPES:
            with self.subTest(repository=repository):
                config = self.config(repository)
                plan = create_plan(
                    config,
                    event_name="push",
                    sha=workflow_sha,
                    changed_paths=[],
                    sha_resolver=self.resolve_sha,
                )

                self.assertEqual(
                    plan.target_environments,
                    (config.environments.development,),
                )
                self.assertEqual(len(plan.deployments), 1)
                self.assertEqual(
                    {entry["name"] for entry in plan.required_builds},
                    {build.name for build in config.builds},
                )

    def test_legacy_imports_forward_to_namespaced_implementations(self) -> None:
        self.assertIs(legacy_execute_build, execute_build)
        self.assertIs(legacy_create_plan, create_plan)


if __name__ == "__main__":
    unittest.main()
