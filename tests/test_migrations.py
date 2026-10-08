from __future__ import annotations

import copy
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from test_deploy import AwsRunner

from canadalogin_release.config import ConfigError, PipelineConfig
from canadalogin_release.pipeline.deploy import migrate_ecs, preflight_ecs
from canadalogin_release.support.commands import CommandError
from canadalogin_release.support.runtime import RuntimeContext

CONFIG = """
schema_version: 2
application: migration-test
profile: ecs-service
environments: [dev]
backend:
  dockerfile: Dockerfile
  migrations: true
"""
REPOSITORY = "123456789012.dkr.ecr.ca-central-1.amazonaws.com/app"
NETWORK = {
    "awsvpcConfiguration": {
        "subnets": ["subnet-private"],
        "securityGroups": ["sg-database"],
        "assignPublicIp": "DISABLED",
    }
}
MIGRATION_DEFINITION = {
    "family": "app-migrations",
    "taskDefinitionArn": "migration:1",
    "revision": 1,
    "status": "ACTIVE",
    "networkMode": "awsvpc",
    "requiresCompatibilities": ["FARGATE"],
    "taskRoleArn": "arn:aws:iam::123456789012:role/migrator",
    "executionRoleArn": "arn:aws:iam::123456789012:role/migrator-execution",
    "cpu": "256",
    "memory": "512",
    "containerDefinitions": [
        {
            "name": "migrations",
            "essential": True,
            "image": f"{REPOSITORY}:old",
            "command": ["incorrect-command"],
            "workingDirectory": "/code",
            "environment": [{"name": "POSTGRES_USER", "value": "migrator"}],
            "secrets": [{"name": "CONFIG", "valueFrom": "secret-arn"}],
            "logConfiguration": {"logDriver": "awslogs"},
            "healthCheck": {"command": ["CMD", "false"]},
            "restartPolicy": {"enabled": True},
        }
    ],
}


class MigrationTest(unittest.TestCase):
    def config(self, content: str = CONFIG) -> PipelineConfig:
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "config.yml"
            filename.write_text(content)
            return PipelineConfig.load(filename)

    def context(self, **variables: str) -> RuntimeContext:
        return RuntimeContext.create(
            repository=".",
            environment="dev",
            sha="abc123",
            release_tag=None,
            github_ref="",
            variables={
                "RELEASE_ECR_REPOSITORY": REPOSITORY,
                "RELEASE_ECS_CLUSTER": "cluster",
                "RELEASE_ECS_SERVICE": "web",
                "RELEASE_ECS_CONTAINER": "web",
                "RELEASE_ECS_MIGRATION_TASK_DEFINITION": "app-migrations",
                **variables,
            },
        )

    def preflight_responses(self, *, definition=None, network=None, services=1):
        responses = [
            (0, json.dumps({"imageDetails": [{"imageDigest": "sha256:candidate"}]}))
        ]
        for _ in range(services):
            responses.extend(
                [
                    (
                        0,
                        json.dumps(
                            {
                                "services": [
                                    {
                                        "taskDefinition": "web:1",
                                        "networkConfiguration": NETWORK
                                        if network is None
                                        else network,
                                    }
                                ]
                            }
                        ),
                    ),
                    (
                        0,
                        json.dumps(
                            {
                                "taskDefinition": {
                                    "family": "web",
                                    "containerDefinitions": [
                                        {
                                            "name": "web",
                                            "image": f"{REPOSITORY}:abc123",
                                        }
                                    ],
                                }
                            }
                        ),
                    ),
                ]
            )
        responses.append(
            (
                0,
                json.dumps(
                    {
                        "taskDefinition": MIGRATION_DEFINITION
                        if definition is None
                        else definition
                    }
                ),
            )
        )
        return responses

    def task(
        self,
        *,
        exit_code=0,
        stop_code="EssentialContainerExited",
        status="STOPPED",
        arn="task:migrate",
    ):
        container = {"name": "migrations"}
        if exit_code is not None:
            container["exitCode"] = exit_code
        return json.dumps(
            {
                "tasks": [
                    {
                        "taskArn": arn,
                        "lastStatus": status,
                        "stopCode": stop_code,
                        "stoppedReason": "task reason",
                        "containers": [container],
                    }
                ]
            }
        )

    def runner(self, *task_responses, **preflight):
        return AwsRunner(
            [
                *self.preflight_responses(**preflight),
                (
                    0,
                    json.dumps(
                        {"taskDefinition": {"taskDefinitionArn": "migration:2"}}
                    ),
                ),
                (
                    0,
                    json.dumps(
                        {"tasks": [{"taskArn": "task:migrate"}], "failures": []}
                    ),
                ),
                *((0, response) for response in task_responses),
                (0, "{}"),
                (0, self.task()),
                (0, "{}"),
            ]
        )

    def test_disabled_migrations_do_not_call_aws(self):
        runner = AwsRunner()
        result = migrate_ecs(
            self.config(CONFIG.replace("true", "false")), self.context(), runner=runner
        )
        self.assertEqual(runner.commands, [])
        self.assertEqual(result.changed_resources, ())

    def test_success_uses_candidate_digest_and_dedicated_identity(self):
        runner = self.runner(self.task(status="RUNNING"), self.task())
        with patch("canadalogin_release.pipeline.deploy.time.sleep"):
            result = migrate_ecs(self.config(), self.context(), runner=runner)

        registration = runner.registration
        self.assertEqual(
            registration["taskRoleArn"], MIGRATION_DEFINITION["taskRoleArn"]
        )
        self.assertEqual(
            registration["executionRoleArn"], MIGRATION_DEFINITION["executionRoleArn"]
        )
        container = registration["containerDefinitions"][0]
        self.assertEqual(container["image"], f"{REPOSITORY}@sha256:candidate")
        self.assertEqual(container["command"], ["alembic", "upgrade", "head"])
        for field in ("environment", "secrets", "workingDirectory", "logConfiguration"):
            self.assertEqual(
                container[field], MIGRATION_DEFINITION["containerDefinitions"][0][field]
            )
        self.assertNotIn("healthCheck", container)
        self.assertNotIn("restartPolicy", container)
        self.assertNotIn("revision", registration)
        command = next(command for command in runner.commands if "run-task" in command)
        self.assertIn("FARGATE", command)
        self.assertEqual(
            json.loads(command[command.index("--network-configuration") + 1]), NETWORK
        )
        self.assertEqual(command[command.index("--count") + 1], "1")
        self.assertEqual(runner.commands[-1][2], "deregister-task-definition")
        self.assertFalse(any("stop-task" in command for command in runner.commands))
        self.assertFalse(
            any(
                "update-service" in command or "put-parameter" in command
                for command in runner.commands
            )
        )
        self.assertTrue(all(not value for value in runner.log_outputs))
        self.assertEqual(result.changed_resources, ("ecs-migration:cluster/backend",))

    def test_multiple_services_run_one_migration_even_for_same_image(self):
        config = self.config(CONFIG + "  services: [web, worker]\n")
        context = self.context(
            **{
                f"RELEASE_ECS_{name}_{field}": value
                for name in ("WEB", "WORKER")
                for field, value in (
                    ("CLUSTER", "cluster"),
                    ("SERVICE", name.lower()),
                    ("CONTAINER", "web"),
                )
            }
        )
        runner = self.runner(self.task(), services=2)
        migrate_ecs(config, context, runner=runner)
        self.assertEqual(sum("run-task" in command for command in runner.commands), 1)

    def test_preflight_is_read_only_and_validates_migration_definition(self):
        runner = AwsRunner(self.preflight_responses())
        preflight_ecs(self.config(), self.context(), runner=runner)
        self.assertTrue(
            all(command[2].startswith("describe-") for command in runner.commands)
        )

    def test_missing_variable_fails_before_registration(self):
        runner = AwsRunner(self.preflight_responses()[:-1])
        context = self.context()
        del context.variables["RELEASE_ECS_MIGRATION_TASK_DEFINITION"]
        with self.assertRaisesRegex(
            ConfigError, "RELEASE_ECS_MIGRATION_TASK_DEFINITION"
        ):
            migrate_ecs(self.config(), context, runner=runner)
        self.assertFalse(
            any("register-task-definition" in command for command in runner.commands)
        )

    def test_bad_task_contract_fails_before_mutation(self):
        for field, value in (
            ("networkMode", "bridge"),
            ("requiresCompatibilities", ["EC2"]),
            ("taskRoleArn", ""),
            ("executionRoleArn", ""),
            ("containerDefinitions", []),
            ("containerDefinitions", [{"name": "web"}]),
            ("containerDefinitions", [{"name": "migrations", "essential": False}]),
            ("containerDefinitions", [{"name": "migrations"}, {"name": "sidecar"}]),
        ):
            with self.subTest(field=field, value=value):
                definition = copy.deepcopy(MIGRATION_DEFINITION)
                definition[field] = value
                runner = AwsRunner(self.preflight_responses(definition=definition))
                with self.assertRaises(ConfigError):
                    migrate_ecs(self.config(), self.context(), runner=runner)
                self.assertFalse(
                    any(
                        "register-task-definition" in command
                        for command in runner.commands
                    )
                )

    def test_missing_network_fails_before_mutation(self):
        runner = AwsRunner(self.preflight_responses(network={}))
        with self.assertRaisesRegex(ConfigError, "awsvpc subnets"):
            migrate_ecs(self.config(), self.context(), runner=runner)
        self.assertFalse(any("run-task" in command for command in runner.commands))

    def test_missing_candidate_digest_fails_before_mutation(self):
        runner = AwsRunner(self.preflight_responses())
        runner.responses[0] = (0, '{"imageDetails": [{"imageTags": ["abc123"]}]}')
        with self.assertRaisesRegex(ConfigError, "no image digest"):
            migrate_ecs(self.config(), self.context(), runner=runner)
        self.assertEqual(len(runner.commands), 1)

    def test_exit_or_startup_failure_aborts_and_cleans_up(self):
        for code, stop_code in (
            (1, "EssentialContainerExited"),
            (None, "TaskFailedToStart"),
            (0, "UserInitiated"),
        ):
            with self.subTest(code=code, stop_code=stop_code):
                runner = self.runner(self.task(exit_code=code, stop_code=stop_code))
                with self.assertRaisesRegex(ConfigError, "Migration task.*failed"):
                    migrate_ecs(self.config(), self.context(), runner=runner)
                self.assertEqual(
                    [command[2] for command in runner.commands[-3:]],
                    ["stop-task", "describe-tasks", "deregister-task-definition"],
                )
                self.assertFalse(
                    any("update-service" in command for command in runner.commands)
                )

    def test_run_task_failure_deregisters_revision(self):
        runner = self.runner()
        runner.responses[len(self.preflight_responses()) + 1] = (
            0,
            json.dumps({"tasks": [], "failures": [{"reason": "capacity"}]}),
        )
        with self.assertRaisesRegex(ConfigError, "could not start.*capacity"):
            migrate_ecs(self.config(), self.context(), runner=runner)
        self.assertEqual(runner.commands[-1][2], "deregister-task-definition")
        self.assertFalse(any("stop-task" in command for command in runner.commands))

    def test_unexpected_task_or_describe_failure_aborts(self):
        for response in (
            self.task(arn="other-task"),
            '{"tasks": [], "failures": [{"reason": "ACCESS_DENIED"}]}',
            "not-json",
        ):
            with self.subTest(response=response):
                runner = self.runner(response)
                with self.assertRaises(ConfigError):
                    migrate_ecs(self.config(), self.context(), runner=runner)
                self.assertEqual(runner.commands[-3][2], "stop-task")

    def test_task_visibility_lag_retries_observation_not_execution(self):
        runner = self.runner(
            '{"tasks": [], "failures": [{"reason": "MISSING"}]}',
            '{"tasks": [], "failures": []}',
            self.task(),
        )
        with patch("canadalogin_release.pipeline.deploy.time.sleep"):
            migrate_ecs(self.config(), self.context(), runner=runner)
        self.assertEqual(sum("run-task" in command for command in runner.commands), 1)
        self.assertEqual(
            sum("describe-tasks" in command for command in runner.commands), 3
        )

    def test_stop_is_confirmed_before_releasing_lock(self):
        runner = self.runner(self.task(exit_code=1))
        runner.responses[-2:-1] = [(0, self.task(status="RUNNING")), (0, self.task())]
        with (
            patch("canadalogin_release.pipeline.deploy.time.sleep"),
            self.assertRaisesRegex(ConfigError, "exit_code=1"),
        ):
            migrate_ecs(self.config(), self.context(), runner=runner)
        self.assertEqual(
            [command[2] for command in runner.commands[-4:]],
            [
                "stop-task",
                "describe-tasks",
                "describe-tasks",
                "deregister-task-definition",
            ],
        )

    def test_timeout_stops_task_and_deregisters_revision(self):
        runner = self.runner(self.task(status="RUNNING"))
        with (
            patch(
                "canadalogin_release.pipeline.deploy.time.monotonic",
                side_effect=[0, 0, 901, 1000, 1000],
            ),
            self.assertRaisesRegex(ConfigError, "timed out"),
        ):
            migrate_ecs(self.config(), self.context(), runner=runner)
        self.assertEqual(
            [command[2] for command in runner.commands[-3:]],
            ["stop-task", "describe-tasks", "deregister-task-definition"],
        )

    def test_cleanup_failure_preserves_original_failure(self):
        runner = self.runner(self.task(exit_code=1))
        runner.responses[-3:] = [(1, "cleanup failed"), (1, "cleanup failed")]
        with self.assertRaisesRegex(ConfigError, "exit_code=1"):
            migrate_ecs(self.config(), self.context(), runner=runner)

    def test_unconfirmed_stop_warns_and_preserves_original_failure(self):
        runner = self.runner(self.task(exit_code=1))
        output = StringIO()
        with (
            patch(
                "canadalogin_release.pipeline.deploy.ECS_MIGRATION_CLEANUP_TIMEOUT_SECONDS",
                0,
            ),
            redirect_stdout(output),
            self.assertRaisesRegex(ConfigError, "exit_code=1"),
        ):
            migrate_ecs(self.config(), self.context(), runner=runner)
        self.assertIn("unsafe to retry", output.getvalue())
        self.assertEqual(runner.commands[-1][2], "deregister-task-definition")

    def test_command_failure_still_cleans_up(self):
        runner = self.runner()
        run = runner.run

        def fail_describe(arguments, **kwargs):
            if "describe-tasks" in arguments:
                raise CommandError("AWS command failed")
            return run(arguments, **kwargs)

        runner.run = fail_describe
        with self.assertRaises(CommandError):
            migrate_ecs(self.config(), self.context(), runner=runner)
        self.assertEqual(runner.commands[-2][2], "stop-task")
