from __future__ import annotations

import json
import subprocess
import unittest
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from canadalogin_release.config import ConfigError, PipelineConfig
from canadalogin_release.deploy import deploy_ecs, deploy_s3
from canadalogin_release.runtime import RuntimeContext

EXAMPLES = Path(__file__).parents[1] / "examples"


class AwsRunner:
    def __init__(self, responses: Sequence[tuple[int, str]] = ()) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.unset_environments: list[tuple[str, ...]] = []
        self.responses = list(responses)
        self.registration: dict[str, object] | None = None

    def run(
        self,
        arguments: Sequence[str],
        *,
        cwd: str | Path = ".",
        environment: Mapping[str, str] | None = None,
        unset_environment: Sequence[str] = (),
        check: bool = True,
        log_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = tuple(arguments)
        self.commands.append(command)
        self.unset_environments.append(tuple(unset_environment))
        if "register-task-definition" in command:
            input_path = next(
                argument.removeprefix("file://")
                for argument in command
                if argument.startswith("file://")
            )
            self.registration = json.loads(Path(input_path).read_text())
        return_code, output = self.responses.pop(0) if self.responses else (0, "{}")
        return subprocess.CompletedProcess(arguments, return_code, output, "")


class DeployTest(unittest.TestCase):
    def config(self, repository: str) -> PipelineConfig:
        return PipelineConfig.load(EXAMPLES / repository / "release-pipeline.toml")

    def context(
        self,
        repository: Path,
        variables: Mapping[str, str],
        secrets: Mapping[str, str] | None = None,
    ) -> RuntimeContext:
        return RuntimeContext.create(
            repository=repository,
            environment="dev",
            sha="abc123",
            release_tag=None,
            github_ref="refs/heads/main",
            variables=variables,
            secrets=secrets,
        )

    def test_s3_preflight_happens_before_sync_and_invalidation(self) -> None:
        config = self.config("gc-signin-static-website")
        runner = AwsRunner(
            [
                (0, "object\n"),
                (0, ""),
                (0, ""),
                (0, "Deployed\n"),
                (0, "Deployed\n"),
            ]
        )
        context = self.context(
            Path("."),
            {
                "STATIC_WEBSITE_BUILD_ARTIFACTS_S3_BUCKET": "builds",
                "EN_S3_BUCKET": "english",
                "FR_S3_BUCKET": "french",
                "EN_CLOUDFRONT_DISTRIBUTION_ID": "EN123",
                "FR_CLOUDFRONT_DISTRIBUTION_ID": "FR123",
            },
        )

        result = deploy_s3(config, context, runner=runner)

        self.assertEqual(runner.commands[0], ("aws", "s3", "ls", "s3://builds/abc123/"))
        self.assertIn("DEPLOY_SECRET_1", runner.unset_environments[0])
        self.assertEqual(
            runner.commands[5],
            ("aws", "s3", "sync", "s3://builds/abc123", "s3://english", "--delete"),
        )
        self.assertEqual(runner.commands[1][0:3], ("aws", "s3api", "head-bucket"))
        self.assertEqual(runner.commands[2][0:3], ("aws", "s3api", "head-bucket"))
        self.assertEqual(
            runner.commands[3][0:3], ("aws", "cloudfront", "get-distribution")
        )
        self.assertEqual(
            runner.commands[4][0:3], ("aws", "cloudfront", "get-distribution")
        )
        self.assertIn("EN123", runner.commands[7])
        self.assertEqual(result.deployment_sha, "abc123")

    def test_missing_second_s3_target_aborts_before_first_sync(self) -> None:
        config = self.config("gc-signin-static-website")
        runner = AwsRunner([(0, "object\n"), (0, "")])
        context = self.context(
            Path("."),
            {
                "STATIC_WEBSITE_BUILD_ARTIFACTS_S3_BUCKET": "builds",
                "EN_S3_BUCKET": "english",
                "EN_CLOUDFRONT_DISTRIBUTION_ID": "EN123",
                "FR_CLOUDFRONT_DISTRIBUTION_ID": "FR123",
            },
        )

        with self.assertRaisesRegex(ConfigError, "FR_S3_BUCKET"):
            deploy_s3(config, context, runner=runner)

        self.assertFalse(
            any(command[1:3] == ("s3", "sync") for command in runner.commands)
        )

    def test_inaccessible_distribution_aborts_before_s3_sync(self) -> None:
        config = self.config("gc-signin-static-website")
        runner = AwsRunner(
            [
                (0, "object\n"),
                (0, ""),
                (0, ""),
                (0, "Deployed\n"),
                (1, ""),
            ]
        )
        context = self.context(
            Path("."),
            {
                "STATIC_WEBSITE_BUILD_ARTIFACTS_S3_BUCKET": "builds",
                "EN_S3_BUCKET": "english",
                "FR_S3_BUCKET": "french",
                "EN_CLOUDFRONT_DISTRIBUTION_ID": "EN123",
                "FR_CLOUDFRONT_DISTRIBUTION_ID": "FR123",
            },
        )

        with self.assertRaisesRegex(ConfigError, "FR123.*inaccessible"):
            deploy_s3(config, context, runner=runner)

        self.assertFalse(
            any(command[1:3] == ("s3", "sync") for command in runner.commands)
        )

    def test_rendered_empty_s3_prefix_is_forbidden(self) -> None:
        config = self.config("gc-signin-static-website")
        deployment = replace(config.deployments[0], artifact_prefix="{release_tag}")
        config = replace(config, deployments=(deployment,))
        runner = AwsRunner()
        context = self.context(
            Path("."),
            {
                "STATIC_WEBSITE_BUILD_ARTIFACTS_S3_BUCKET": "builds",
                "EN_S3_BUCKET": "english",
                "FR_S3_BUCKET": "french",
                "EN_CLOUDFRONT_DISTRIBUTION_ID": "EN123",
                "FR_CLOUDFRONT_DISTRIBUTION_ID": "FR123",
            },
        )

        with self.assertRaisesRegex(ConfigError, "rendered empty"):
            deploy_s3(config, context, runner=runner)

        self.assertEqual(runner.commands, [])

    def test_empty_s3_prefix_aborts_before_mutation(self) -> None:
        config = self.config("gc-signin-static-website")
        runner = AwsRunner([(0, "")])
        context = self.context(
            Path("."),
            {
                "STATIC_WEBSITE_BUILD_ARTIFACTS_S3_BUCKET": "builds",
                "EN_S3_BUCKET": "english",
                "FR_S3_BUCKET": "french",
                "EN_CLOUDFRONT_DISTRIBUTION_ID": "EN123",
                "FR_CLOUDFRONT_DISTRIBUTION_ID": "FR123",
            },
        )

        with self.assertRaisesRegex(ConfigError, "No build artifact exists"):
            deploy_s3(config, context, runner=runner)

        self.assertEqual(runner.commands, [("aws", "s3", "ls", "s3://builds/abc123/")])

    def test_ecs_same_image_is_a_no_op(self) -> None:
        config = self.config("gc-signin-migration-oidc-rp-simulator")
        image = json.dumps({"imageDetails": [{"imageTags": ["abc123"]}]})
        service = json.dumps(
            {"services": [{"taskDefinition": "task:1"}], "failures": []}
        )
        task = json.dumps(
            {
                "taskDefinition": {
                    "family": "example",
                    "containerDefinitions": [
                        {"name": "service", "image": "example.ecr/app:abc123"}
                    ],
                }
            }
        )
        runner = AwsRunner([(0, image), (0, service), (0, task)])
        context = self.context(
            Path("."),
            {
                "ARTIFACT_ECR_REPOSITORY": "example.ecr/app",
                "ECS_CLUSTER": "cluster",
                "ECS_SERVICE": "service",
            },
        )

        result = deploy_ecs(config, context, runner=runner)

        self.assertEqual(len(runner.commands), 5)
        self.assertIn("services-stable", runner.commands[3])
        self.assertEqual(runner.commands[4][0:3], ("aws", "ssm", "put-parameter"))
        self.assertEqual(result.unchanged_resources, ("ecs:cluster/service",))

    def test_missing_ecr_image_aborts_before_service_mutation(self) -> None:
        config = self.config("gc-signin-migration-oidc-rp-simulator")
        runner = AwsRunner([(0, json.dumps({"imageDetails": []}))])
        context = self.context(
            Path("."),
            {
                "ARTIFACT_ECR_REPOSITORY": "example.ecr/app",
                "ECS_CLUSTER": "cluster",
                "ECS_SERVICE": "service",
            },
        )

        with self.assertRaisesRegex(ConfigError, "does not exist"):
            deploy_ecs(config, context, runner=runner)

        self.assertEqual(runner.commands[0][0:3], ("aws", "ecr", "describe-images"))
        self.assertEqual(len(runner.commands), 1)

    def test_invalid_ssm_template_aborts_before_ecs_mutation(self) -> None:
        config = self.config("gc-signin-migration-oidc-rp-simulator")
        service_config = replace(
            config.deployments[0].services[0], ssm_parameter="/ecs/{unknown}"
        )
        deployment = replace(config.deployments[0], services=(service_config,))
        config = replace(config, deployments=(deployment,))
        image = json.dumps({"imageDetails": [{"imageTags": ["abc123"]}]})
        service = json.dumps(
            {"services": [{"taskDefinition": "task:1"}], "failures": []}
        )
        task = json.dumps(
            {
                "taskDefinition": {
                    "family": "example",
                    "containerDefinitions": [
                        {"name": "service", "image": "example.ecr/app:old"}
                    ],
                }
            }
        )
        runner = AwsRunner([(0, image), (0, service), (0, task)])
        context = self.context(
            Path("."),
            {
                "ARTIFACT_ECR_REPOSITORY": "example.ecr/app",
                "ECS_CLUSTER": "cluster",
                "ECS_SERVICE": "service",
            },
        )

        with self.assertRaisesRegex(ConfigError, "Unknown template fields"):
            deploy_ecs(config, context, runner=runner)

        commands = " ".join(" ".join(command) for command in runner.commands)
        self.assertNotIn("register-task-definition", commands)
        self.assertNotIn("update-service", commands)

    def test_ecs_changed_image_registers_waits_and_updates_ssm(self) -> None:
        config = self.config("gc-signin-migration-oidc-rp-simulator")
        image = json.dumps({"imageDetails": [{"imageTags": ["abc123"]}]})
        service = json.dumps(
            {"services": [{"taskDefinition": "task:1"}], "failures": []}
        )
        task = json.dumps(
            {
                "taskDefinition": {
                    "taskDefinitionArn": "task:1",
                    "revision": 1,
                    "status": "ACTIVE",
                    "family": "example",
                    "networkMode": "awsvpc",
                    "containerDefinitions": [
                        {"name": "service", "image": "example.ecr/app:old"},
                        {"name": "sidecar", "image": "example.ecr/sidecar:fixed"},
                    ],
                }
            }
        )
        registered = json.dumps({"taskDefinition": {"taskDefinitionArn": "task:2"}})
        runner = AwsRunner(
            [
                (0, image),
                (0, service),
                (0, task),
                (0, registered),
                (0, "{}"),
                (0, "{}"),
                (0, "{}"),
            ]
        )
        context = self.context(
            Path("."),
            {
                "ARTIFACT_ECR_REPOSITORY": "example.ecr/app",
                "ECS_CLUSTER": "cluster",
                "ECS_SERVICE": "service",
            },
        )

        result = deploy_ecs(config, context, runner=runner)

        self.assertNotIn("revision", runner.registration)
        containers = runner.registration["containerDefinitions"]
        self.assertEqual(containers[0]["image"], "example.ecr/app:abc123")
        self.assertEqual(containers[1]["image"], "example.ecr/sidecar:fixed")
        self.assertNotIn("tags", runner.registration)
        self.assertIn("task:2", runner.commands[4])
        self.assertIn("--propagate-tags", runner.commands[4])
        self.assertIn("SERVICE", runner.commands[4])
        self.assertIn("services-stable", runner.commands[5])
        self.assertEqual(runner.commands[6][0:3], ("aws", "ssm", "put-parameter"))
        self.assertEqual(result.changed_resources, ("ecs:cluster/service",))

    def test_force_redeploy_uses_current_task_definition(self) -> None:
        config = self.config("gc-signin-migration-oidc-rp-simulator")
        image = json.dumps({"imageDetails": [{"imageTags": ["abc123"]}]})
        service = json.dumps(
            {"services": [{"taskDefinition": "task:1"}], "failures": []}
        )
        task = json.dumps(
            {
                "taskDefinition": {
                    "family": "example",
                    "containerDefinitions": [
                        {"name": "service", "image": "example.ecr/app:abc123"}
                    ],
                }
            }
        )
        runner = AwsRunner(
            [(0, image), (0, service), (0, task), (0, "{}"), (0, "{}"), (0, "{}")]
        )
        context = self.context(
            Path("."),
            {
                "ARTIFACT_ECR_REPOSITORY": "example.ecr/app",
                "ECS_CLUSTER": "cluster",
                "ECS_SERVICE": "service",
            },
        )

        deploy_ecs(config, context, force_redeploy=True, runner=runner)

        self.assertIn("--force-new-deployment", runner.commands[3])
        self.assertNotIn(
            "register-task-definition",
            " ".join(" ".join(command) for command in runner.commands),
        )


if __name__ == "__main__":
    unittest.main()
