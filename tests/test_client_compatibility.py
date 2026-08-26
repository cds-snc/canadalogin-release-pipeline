from __future__ import annotations

import unittest
from pathlib import Path

from canadalogin_release.config import PipelineConfig
from canadalogin_release.planner import create_plan

EXAMPLES = Path(__file__).parents[1] / "examples"
CLIENT_SHAPES = {
    "gc-signin-user-selfservice-webapp": {
        "builds": ("frontend", "backend", "load-test"),
        "deployment_kinds": ("s3", "ecs"),
        "ecs_services": 1,
        "s3_targets": 1,
        "cloudfront_invalidations": 1,
    },
    "gc-signin-static-website": {
        "builds": ("website",),
        "deployment_kinds": ("s3",),
        "ecs_services": 0,
        "s3_targets": 2,
        "cloudfront_invalidations": 2,
    },
    "gc-signin-partner-portal": {
        "builds": ("frontend", "backend"),
        "deployment_kinds": ("s3", "ecs"),
        "ecs_services": 2,
        "s3_targets": 1,
        "cloudfront_invalidations": 1,
    },
    "gc-sign-in-migration": {
        "builds": ("frontend", "backend", "load-test"),
        "deployment_kinds": ("s3", "ecs"),
        "ecs_services": 1,
        "s3_targets": 1,
        "cloudfront_invalidations": 1,
    },
    "gc-signin-migration-oidc-rp-simulator": {
        "builds": ("backend",),
        "deployment_kinds": ("ecs",),
        "ecs_services": 1,
        "s3_targets": 0,
        "cloudfront_invalidations": 0,
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

    def test_push_plans_cover_every_client_environment_and_build(self) -> None:
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

                self.assertEqual(plan.target_environments, config.environments.deploy)
                self.assertEqual(len(plan.deployments), len(config.environments.deploy))
                self.assertEqual(
                    {entry["name"] for entry in plan.required_builds},
                    {build.name for build in config.builds if build.gates_deployment},
                )
                self.assertEqual(
                    {entry["name"] for entry in plan.auxiliary_builds},
                    {
                        build.name
                        for build in config.builds
                        if not build.gates_deployment
                    },
                )


if __name__ == "__main__":
    unittest.main()
