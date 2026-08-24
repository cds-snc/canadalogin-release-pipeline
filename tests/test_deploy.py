from __future__ import annotations

import json
import subprocess
import unittest
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from canadalogin_release.config import ConfigError, PipelineConfig
from canadalogin_release.deploy import (
    _ecs_rollout_failed,
    _ecs_service_is_stable,
    deploy_ecs,
    deploy_s3,
)
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
        return PipelineConfig.load(
            EXAMPLES / repository / "release-pipeline-configuration.yml"
        )

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

    @staticmethod
    def stable_service(
        task_definition: str = "task:1", deployment_id: str = "deployment-1"
    ) -> str:
        return json.dumps(
            {
                "services": [
                    {
                        "runningCount": 1,
                        "desiredCount": 1,
                        "pendingCount": 0,
                        "deployments": [
                            {
                                "id": deployment_id,
                                "status": "PRIMARY",
                                "taskDefinition": task_definition,
                                "rolloutState": "COMPLETED",
                            }
                        ],
                    }
                ],
                "failures": [],
            }
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
                "RELEASE_STATIC_ARTIFACT_BUCKET": "builds",
                "RELEASE_SITE_EN_BUCKET": "english",
                "RELEASE_SITE_FR_BUCKET": "french",
                "RELEASE_SITE_EN_DISTRIBUTION_ID": "EN123",
                "RELEASE_SITE_FR_DISTRIBUTION_ID": "FR123",
            },
        )

        result = deploy_s3(config, context, runner=runner)

        self.assertEqual(runner.commands[0], ("aws", "s3", "ls", "s3://builds/abc123/"))
        self.assertNotIn("DEPLOY_SECRET_1", runner.unset_environments[0])
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
                "RELEASE_STATIC_ARTIFACT_BUCKET": "builds",
                "RELEASE_SITE_EN_BUCKET": "english",
                "RELEASE_SITE_EN_DISTRIBUTION_ID": "EN123",
                "RELEASE_SITE_FR_DISTRIBUTION_ID": "FR123",
            },
        )

        with self.assertRaisesRegex(ConfigError, "RELEASE_SITE_FR_BUCKET"):
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
                "RELEASE_STATIC_ARTIFACT_BUCKET": "builds",
                "RELEASE_SITE_EN_BUCKET": "english",
                "RELEASE_SITE_FR_BUCKET": "french",
                "RELEASE_SITE_EN_DISTRIBUTION_ID": "EN123",
                "RELEASE_SITE_FR_DISTRIBUTION_ID": "FR123",
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
                "RELEASE_STATIC_ARTIFACT_BUCKET": "builds",
                "RELEASE_SITE_EN_BUCKET": "english",
                "RELEASE_SITE_FR_BUCKET": "french",
                "RELEASE_SITE_EN_DISTRIBUTION_ID": "EN123",
                "RELEASE_SITE_FR_DISTRIBUTION_ID": "FR123",
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
                "RELEASE_STATIC_ARTIFACT_BUCKET": "builds",
                "RELEASE_SITE_EN_BUCKET": "english",
                "RELEASE_SITE_FR_BUCKET": "french",
                "RELEASE_SITE_EN_DISTRIBUTION_ID": "EN123",
                "RELEASE_SITE_FR_DISTRIBUTION_ID": "FR123",
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
        runner = AwsRunner(
            [(0, image), (0, service), (0, task), (0, self.stable_service())]
        )
        context = self.context(
            Path("."),
            {
                "RELEASE_ECR_REPOSITORY": "example.ecr/app",
                "RELEASE_ECS_CLUSTER": "cluster",
                "RELEASE_ECS_SERVICE": "service",
                "RELEASE_ECS_CONTAINER": "service",
            },
        )

        result = deploy_ecs(config, context, runner=runner)

        self.assertEqual(len(runner.commands), 5)
        self.assertEqual(runner.commands[3][0:3], ("aws", "ecs", "describe-services"))
        self.assertEqual(runner.commands[4][0:3], ("aws", "ssm", "put-parameter"))
        self.assertEqual(result.unchanged_resources, ("ecs:cluster/service",))

    def test_ecs_digest_image_is_a_no_op(self) -> None:
        config = self.config("gc-signin-migration-oidc-rp-simulator")
        repository = "123456789012.dkr.ecr.ca-central-1.amazonaws.com/app"
        image = json.dumps(
            {"imageDetails": [{"imageTags": ["abc123"], "imageDigest": "sha256:abc"}]}
        )
        service = json.dumps(
            {"services": [{"taskDefinition": "task:1"}], "failures": []}
        )
        task = json.dumps(
            {
                "taskDefinition": {
                    "family": "example",
                    "containerDefinitions": [
                        {
                            "name": "service",
                            "image": f"{repository}@sha256:abc",
                        }
                    ],
                }
            }
        )
        runner = AwsRunner(
            [(0, image), (0, service), (0, task), (0, self.stable_service())]
        )
        context = self.context(
            Path("."),
            {
                "RELEASE_ECR_REPOSITORY": repository,
                "RELEASE_ECS_CLUSTER": "cluster",
                "RELEASE_ECS_SERVICE": "service",
                "RELEASE_ECS_CONTAINER": "service",
            },
        )

        result = deploy_ecs(config, context, runner=runner)

        self.assertEqual(result.unchanged_resources, ("ecs:cluster/service",))
        self.assertNotIn(
            "update-service", " ".join(" ".join(command) for command in runner.commands)
        )

    def test_missing_ecr_image_aborts_before_service_mutation(self) -> None:
        config = self.config("gc-signin-migration-oidc-rp-simulator")
        runner = AwsRunner([(0, json.dumps({"imageDetails": []}))])
        context = self.context(
            Path("."),
            {
                "RELEASE_ECR_REPOSITORY": "example.ecr/app",
                "RELEASE_ECS_CLUSTER": "cluster",
                "RELEASE_ECS_SERVICE": "service",
                "RELEASE_ECS_CONTAINER": "service",
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
                "RELEASE_ECR_REPOSITORY": "example.ecr/app",
                "RELEASE_ECS_CLUSTER": "cluster",
                "RELEASE_ECS_SERVICE": "service",
                "RELEASE_ECS_CONTAINER": "service",
            },
        )

        with self.assertRaisesRegex(ConfigError, "Unknown template fields"):
            deploy_ecs(config, context, runner=runner)

        commands = " ".join(" ".join(command) for command in runner.commands)
        self.assertNotIn("register-task-definition", commands)
        self.assertNotIn("update-service", commands)

    def test_ecs_changed_image_registers_waits_and_updates_ssm(self) -> None:
        config = self.config("gc-signin-migration-oidc-rp-simulator")
        repository = "123456789012.dkr.ecr.ca-central-1.amazonaws.com/app"
        image = json.dumps(
            {"imageDetails": [{"imageTags": ["abc123"], "imageDigest": "sha256:abc"}]}
        )
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
                        {"name": "service", "image": f"{repository}:old"},
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
                (0, self.stable_service("task:2", "deployment-2")),
                (
                    0,
                    json.dumps(
                        {
                            "taskDefinition": {
                                "containerDefinitions": [
                                    {
                                        "name": "service",
                                        "image": f"{repository}@sha256:abc",
                                    }
                                ]
                            }
                        }
                    ),
                ),
            ]
        )
        context = self.context(
            Path("."),
            {
                "RELEASE_ECR_REPOSITORY": repository,
                "RELEASE_ECS_CLUSTER": "cluster",
                "RELEASE_ECS_SERVICE": "service",
                "RELEASE_ECS_CONTAINER": "service",
            },
        )

        result = deploy_ecs(config, context, runner=runner)

        self.assertNotIn("revision", runner.registration)
        containers = runner.registration["containerDefinitions"]
        self.assertEqual(containers[0]["image"], f"{repository}@sha256:abc")
        self.assertEqual(containers[1]["image"], "example.ecr/sidecar:fixed")
        self.assertNotIn("tags", runner.registration)
        self.assertIn("task:2", runner.commands[4])
        self.assertIn("--propagate-tags", runner.commands[4])
        self.assertIn("SERVICE", runner.commands[4])
        self.assertEqual(runner.commands[5][0:3], ("aws", "ecs", "describe-services"))
        self.assertEqual(
            runner.commands[6][0:3], ("aws", "ecs", "describe-task-definition")
        )
        self.assertEqual(runner.commands[7][0:3], ("aws", "ssm", "put-parameter"))
        self.assertEqual(result.changed_resources, ("ecs:cluster/service",))

    def test_ecs_wait_failure_includes_rollout_diagnostics(self) -> None:
        config = self.config("gc-signin-migration-oidc-rp-simulator")
        image = json.dumps({"imageDetails": [{"imageTags": ["abc123"]}]})
        service = json.dumps(
            {
                "services": [
                    {
                        "taskDefinition": "task:1",
                        "deployments": [{"id": "deployment-1", "status": "PRIMARY"}],
                    }
                ],
                "failures": [],
            }
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
        registered = json.dumps({"taskDefinition": {"taskDefinitionArn": "task:2"}})
        diagnostics = json.dumps(
            {
                "services": [
                    {
                        "runningCount": 1,
                        "desiredCount": 1,
                        "pendingCount": 0,
                        "deployments": [
                            {
                                "taskDefinition": "task:2",
                                "status": "PRIMARY",
                                "rolloutState": "FAILED",
                                "rolloutStateReason": "deployment circuit breaker",
                                "failedTasks": 3,
                            }
                        ],
                        "events": [{"message": "deployment failed"}],
                    }
                ],
                "failures": [],
            }
        )
        runner = AwsRunner(
            [
                (0, image),
                (0, service),
                (0, task),
                (0, registered),
                (0, "{}"),
                (0, diagnostics),
            ]
        )
        context = self.context(
            Path("."),
            {
                "RELEASE_ECR_REPOSITORY": "example.ecr/app",
                "RELEASE_ECS_CLUSTER": "cluster",
                "RELEASE_ECS_SERVICE": "service",
                "RELEASE_ECS_CONTAINER": "service",
            },
        )

        with self.assertRaisesRegex(
            ConfigError,
            "rollout_state.*FAILED.*deployment circuit breaker.*deployment failed",
        ):
            deploy_ecs(config, context, runner=runner)

        self.assertFalse(
            any(command[1:3] == ("ssm", "put-parameter") for command in runner.commands)
        )

    def test_ecs_rollout_failure_ignores_previous_failed_deployment(self) -> None:
        document = {
            "services": [
                {
                    "deployments": [
                        {
                            "id": "deployment-new",
                            "status": "PRIMARY",
                            "taskDefinition": "task:new",
                            "rolloutState": "IN_PROGRESS",
                        },
                        {
                            "id": "deployment-old",
                            "status": "ACTIVE",
                            "taskDefinition": "task:old",
                            "rolloutState": "FAILED",
                        },
                    ]
                }
            ],
            "failures": [],
        }

        self.assertFalse(
            _ecs_rollout_failed(
                document,
                expected_task_definition_arn="task:new",
            )
        )

    def test_ecs_stability_rejects_stale_or_competing_deployment(self) -> None:
        stale = json.loads(self.stable_service("task:old", "deployment-old"))
        self.assertFalse(
            _ecs_service_is_stable(
                stale,
                expected_task_definition_arn="task:new",
            )
        )
        self.assertFalse(
            _ecs_service_is_stable(
                json.loads(self.stable_service("task:new", "deployment-old")),
                expected_task_definition_arn="task:new",
                previous_deployment_id="deployment-old",
            )
        )

    def test_ecs_stability_rejects_malformed_deployment_identity(self) -> None:
        malformed = json.loads(self.stable_service())
        del malformed["services"][0]["deployments"][0]["taskDefinition"]

        self.assertFalse(
            _ecs_service_is_stable(
                malformed,
                expected_task_definition_arn="task:1",
            )
        )

    def test_ecs_wait_timeout_is_bounded_and_includes_last_diagnostics(self) -> None:
        config = self.config("gc-signin-migration-oidc-rp-simulator")
        image = json.dumps({"imageDetails": [{"imageTags": ["abc123"]}]})
        service = json.dumps(
            {
                "services": [
                    {
                        "taskDefinition": "task:1",
                        "deployments": [{"id": "deployment-1", "status": "PRIMARY"}],
                    }
                ],
                "failures": [],
            }
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
        registered = json.dumps({"taskDefinition": {"taskDefinitionArn": "task:2"}})
        pending = json.dumps(
            {
                "services": [
                    {
                        "runningCount": 0,
                        "desiredCount": 1,
                        "pendingCount": 1,
                        "deployments": [
                            {
                                "status": "PRIMARY",
                                "rolloutState": "IN_PROGRESS",
                                "rolloutStateReason": "waiting for task",
                                "failedTasks": 0,
                            }
                        ],
                    }
                ],
                "failures": [],
            }
        )
        runner = AwsRunner(
            [
                (0, image),
                (0, service),
                (0, task),
                (0, registered),
                (0, "{}"),
                (0, pending),
            ]
        )
        context = self.context(
            Path("."),
            {
                "RELEASE_ECR_REPOSITORY": "example.ecr/app",
                "RELEASE_ECS_CLUSTER": "cluster",
                "RELEASE_ECS_SERVICE": "service",
                "RELEASE_ECS_CONTAINER": "service",
            },
        )

        with (
            patch("canadalogin_release.deploy.time.monotonic", side_effect=(0, 601)),
            self.assertRaisesRegex(
                ConfigError,
                "within 600 seconds.*rollout_state.*IN_PROGRESS.*waiting for task",
            ),
        ):
            deploy_ecs(config, context, runner=runner)

        self.assertFalse(
            any(command[1:3] == ("ssm", "put-parameter") for command in runner.commands)
        )

    def test_force_redeploy_uses_current_task_definition(self) -> None:
        config = self.config("gc-signin-migration-oidc-rp-simulator")
        image = json.dumps({"imageDetails": [{"imageTags": ["abc123"]}]})
        service = json.dumps(
            {
                "services": [
                    {
                        "taskDefinition": "task:1",
                        "deployments": [{"id": "deployment-1", "status": "PRIMARY"}],
                    }
                ],
                "failures": [],
            }
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
            [
                (0, image),
                (0, service),
                (0, task),
                (0, "{}"),
                (0, self.stable_service("task:1", "deployment-2")),
            ]
        )
        context = self.context(
            Path("."),
            {
                "RELEASE_ECR_REPOSITORY": "example.ecr/app",
                "RELEASE_ECS_CLUSTER": "cluster",
                "RELEASE_ECS_SERVICE": "service",
                "RELEASE_ECS_CONTAINER": "service",
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
