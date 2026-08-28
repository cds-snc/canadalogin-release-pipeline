from __future__ import annotations

import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from canadalogin_release.config import ConfigError, PipelineConfig, ValueReference
from canadalogin_release.runtime import RuntimeContext, resolve_reference

BASE_CONFIG = """
schema_version: 2
application: Example application
profile: spa-ecs
environments: [dev, test, staging, prod]

frontend:
    environment:
        VITE_API_URL: {secret: VITE_API_BASE_URL}
        VITE_ENVIRONMENT: "{environment}"
    invalidation_paths: [/index.html, /assets/*]

backend:
    dockerfile: backend/Dockerfile
    build_args:
        APP_VERSION: "{release_version}"
"""

SCHEMA_TWO_CONFIG = """
schema_version: 2
application: profile-management
profile: spa-ecs

environments: [dev, test, staging, prod]

frontend:
    environment:
        VITE_BACKEND_API_URL: {var: VITE_BACKEND_API_URL}
        VITE_ENVIRONMENT: "{environment}"
    invalidation_paths: [/index.html, /assets/*]

backend:
    dockerfile: backend/Dockerfile
    build_args:
        RELEASE_TAG: "{release_version}"

load_tests:
    enabled: true
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
        self.assertEqual(config.builds[0].s3_artifact.prefix, "{environment}/{sha}")
        self.assertEqual(config.deployments[0].artifact_prefix, "{environment}/{sha}")
        self.assertEqual(
            config.deployment_roles("prod"),
            {
                "s3": "RELEASE_S3_ROLE",
                "ecs": "RELEASE_ECS_ROLE",
            },
        )
        self.assertEqual(config.deployments[1].services[0].container.source, "var")

    def test_loads_schema_two_spa_ecs_profile(self) -> None:
        config = self.load(SCHEMA_TWO_CONFIG)

        self.assertEqual(config.aws_region, "ca-central-1")
        self.assertEqual(config.environments.development, "dev")
        self.assertEqual(config.environments.versioned, ("test", "staging", "prod"))
        self.assertEqual(
            [build.name for build in config.builds],
            ["frontend", "backend", "load-test"],
        )
        self.assertEqual(
            config.deployment_roles("prod"),
            {"ecs": "RELEASE_ECS_ROLE", "s3": "RELEASE_S3_ROLE"},
        )
        backend = config.builds[1]
        self.assertEqual(backend.docker.context, Path("backend"))
        self.assertEqual(backend.docker.dockerfile, Path("backend/Dockerfile"))
        self.assertEqual(config.builds[2].source_environment, "staging")

    def test_rejects_schema_one(self) -> None:
        with self.assertRaisesRegex(ConfigError, "schema_version must be 2"):
            self.load("schema_version: 1\napplication: old\n")

    def test_schema_two_rejects_slack_configuration(self) -> None:
        invalid = SCHEMA_TWO_CONFIG + "\nnotifications: {}\n"

        with self.assertRaisesRegex(ConfigError, "unknown keys: notifications"):
            self.load(invalid)

    def test_schema_two_rejects_old_context_key(self) -> None:
        with self.assertRaisesRegex(
            ConfigError, "backend contains unknown keys: context"
        ):
            self.load(
                SCHEMA_TWO_CONFIG.replace(
                    "dockerfile: backend/Dockerfile", "context: backend"
                )
            )

    def test_schema_two_rejects_numbered_secret_slots(self) -> None:
        invalid = SCHEMA_TWO_CONFIG.replace(
            "VITE_BACKEND_API_URL: {var: VITE_BACKEND_API_URL}",
            "VITE_BACKEND_API_URL: {secret: BUILD_SECRET_1}",
        )

        with self.assertRaisesRegex(ConfigError, "do not expose"):
            self.load(invalid)

    def test_schema_two_requires_staging_for_load_tests(self) -> None:
        invalid = SCHEMA_TWO_CONFIG.replace(
            "environments: [dev, test, staging, prod]",
            "environments: [dev, test, prod]",
        )

        with self.assertRaisesRegex(ConfigError, "requires the staging environment"):
            self.load(invalid)

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

    def test_resolves_aws_template_values(self) -> None:
        reference = ValueReference.parse(
            "{aws_account_id}.dkr.ecr.{aws_region}.amazonaws.com/example",
            "build.repository",
        )
        context = RuntimeContext.create(
            repository=".",
            environment="dev",
            sha="abc123",
            release_tag=None,
            github_ref="",
        )

        with patch.dict(
            os.environ,
            {"AWS_ACCOUNT_ID": "123456789012", "AWS_REGION": "us-east-1"},
        ):
            self.assertEqual(
                resolve_reference(reference, context),
                "123456789012.dkr.ecr.us-east-1.amazonaws.com/example",
            )

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

    def test_rejects_unknown_repository_dispatch_target(self) -> None:
        invalid = SCHEMA_TWO_CONFIG + """
events:
  repository_dispatch:
    refresh: [ghost]
"""

        with self.assertRaisesRegex(ConfigError, "repository_dispatch.refresh"):
            self.load(invalid)

    def test_all_current_repository_examples_are_valid(self) -> None:
        examples = Path(__file__).parents[1] / "examples"
        paths = sorted(examples.glob("*/release-pipeline-configuration.yml"))

        self.assertEqual(len(paths), 2)
        for path in paths:
            with self.subTest(repository=path.parent.name):
                config = PipelineConfig.load(path)
                self.assertTrue(config.builds)
                self.assertTrue(config.deployments)

if __name__ == "__main__":
    unittest.main()
