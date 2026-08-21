from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from canadalogin_release.config import ConfigError, PipelineConfig, ValueReference

BASE_CONFIG = """
schema_version: 1
application: Example application
environments: {development: dev, deploy: [dev, test, staging, prod], versioned: [test, staging, prod]}
notifications: {info_webhook: {secret: GC_SIGNIN_OPS_SLACK_INFO_WEBHOOK}, alert_webhooks: [{secret: GC_SIGNIN_OPS_SLACK_ALERT_WEBHOOK}]}
builds:
- {name: frontend, kind: command, environments: [dev, test, staging, prod], aws_role: github_action_push_s3, command: {working_directory: frontend, steps: [[npm, ci], [npm, run, build]], environment: {VITE_API_URL: {secret: VITE_API_BASE_URL}, VITE_ENVIRONMENT: "{environment}"}}, s3_artifact: {source: frontend/dist, bucket: {secret: FRONTEND_APP_BUILD_ARTIFACTS_S3_BUCKET}}}
- {name: backend, kind: docker, environments: [dev], aws_role: github_action_push_ecr, dns_audit: true, docker: {context: backend, dockerfile: backend/Dockerfile, repository: {var: ECR_REPOSITORY}, tags: [sha, latest, release], build_args: {APP_VERSION: "{release_version}"}}, sbom: {name: example-backend, dockerfile: backend/Dockerfile}}
deployments:
- {name: frontend, kind: s3, aws_role: github_action_push_s3, artifact_bucket: {secret: FRONTEND_APP_BUILD_ARTIFACTS_S3_BUCKET}, targets: [{bucket: {secret: FRONTEND_APP_S3_BUCKET}, delete: true}], invalidations: [{distribution: {secret: CLOUDFRONT_DISTRIBUTION_ID}, paths: ["/index.html", "/assets/*"]}]}
- {name: backend, kind: ecs, aws_role: github_action_push_ecs, repository: {var: ECR_REPOSITORY}, services: [{cluster: {var: ECS_CLUSTER}, service: {var: ECS_SERVICE}, container: {var: ECS_SERVICE}, ssm_parameter: "/ecs/{cluster}/{service}/container-image"}]}
"""


class PipelineConfigTest(unittest.TestCase):
    def load(self, content: str = BASE_CONFIG) -> PipelineConfig:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "release-pipeline-configuration.yml"
            path.write_text(textwrap.dedent(content))
            return PipelineConfig.load(path)

    def test_rejects_invalid_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "release-pipeline-configuration.yml"
            path.write_text("schema_version: [")

            with self.assertRaisesRegex(ConfigError, "Unable to load"):
                PipelineConfig.load(path)

    def test_loads_s3_and_ecs_application(self) -> None:
        config = self.load()

        self.assertEqual(config.application, "Example application")
        self.assertEqual(
            [build.name for build in config.builds], ["frontend", "backend"]
        )
        self.assertEqual(
            config.deployment_roles("prod"),
            {
                "s3": "github_action_push_s3",
                "ecs": "github_action_push_ecs",
            },
        )
        self.assertEqual(config.deployments[1].services[0].container.source, "var")

    def test_rejects_ambiguous_reference(self) -> None:
        with self.assertRaisesRegex(ConfigError, "exactly one of"):
            ValueReference.parse(
                {"var": "BUCKET", "secret": "BUCKET"}, "artifact.bucket"
            )

    def test_resolves_default_for_missing_variable(self) -> None:
        reference = ValueReference.parse(
            {"var": "OPTIONAL_VALUE", "default": "fallback"}, "build.environment"
        )

        self.assertEqual(reference.resolve({}), "fallback")

    def test_rejects_unknown_configuration_key(self) -> None:
        with self.assertRaisesRegex(ConfigError, "unknown keys: applicaton"):
            self.load(
                BASE_CONFIG.replace(
                    "application: Example application",
                    "application: Example application\napplicaton: typo",
                )
            )

    def test_rejects_secret_the_workflow_does_not_expose(self) -> None:
        with self.assertRaisesRegex(ConfigError, "do not expose"):
            ValueReference.parse(
                {"secret": "UNMAPPED_SECRET"}, "build.environment.CUSTOM"
            )

    def test_rejects_alert_secret_in_info_webhook(self) -> None:
        invalid = BASE_CONFIG.replace(
            "info_webhook: {secret: GC_SIGNIN_OPS_SLACK_INFO_WEBHOOK}",
            "info_webhook: {secret: GC_SIGNIN_OPS_SLACK_ALERT_WEBHOOK}",
        )

        with self.assertRaisesRegex(ConfigError, "do not expose"):
            self.load(invalid)

    def test_rejects_multiple_roles_for_same_deployment_kind(self) -> None:
        invalid = BASE_CONFIG.replace(
            "deployments:\n",
            "deployments:\n"
            "- {name: second-backend, kind: ecs, aws_role: different_ecs_role, repository: {var: ECR_REPOSITORY}, services: [{cluster: {var: ECS_CLUSTER}, service: {var: ECS_SERVICE}, container: {var: ECS_SERVICE}}]}\n",
        )

        with self.assertRaisesRegex(ConfigError, "must use one AWS role"):
            self.load(invalid)

    def test_rejects_unknown_build_environment(self) -> None:
        invalid = BASE_CONFIG.replace(
            "environments: [dev], aws_role: github_action_push_ecr",
            "environments: [ghost], aws_role: github_action_push_ecr",
        )

        with self.assertRaisesRegex(ConfigError, r"builds\[1\].environments"):
            self.load(invalid)

    def test_rejects_unknown_source_environment(self) -> None:
        invalid = BASE_CONFIG.replace(
            "environments: [dev], aws_role: github_action_push_ecr",
            "environments: [dev], source_environment: ghost, aws_role: github_action_push_ecr",
        )

        with self.assertRaisesRegex(ConfigError, "source_environment"):
            self.load(invalid)

    def test_rejects_unknown_repository_dispatch_target(self) -> None:
        invalid = BASE_CONFIG.replace(
            "builds:\n",
            "events:\n  repository_dispatch:\n    refresh: [ghost]\n\nbuilds:\n",
        )

        with self.assertRaisesRegex(ConfigError, "repository_dispatch.refresh"):
            self.load(invalid)

    def test_all_current_repository_examples_are_valid(self) -> None:
        examples = Path(__file__).parents[1] / "examples"
        paths = sorted(examples.glob("*/release-pipeline-configuration.yml"))

        self.assertEqual(len(paths), 5)
        for path in paths:
            with self.subTest(repository=path.parent.name):
                config = PipelineConfig.load(path)
                self.assertTrue(config.builds)
                self.assertTrue(config.deployments)


if __name__ == "__main__":
    unittest.main()
