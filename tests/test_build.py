from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from canadalogin_release.build import execute_build
from canadalogin_release.config import ConfigError, PipelineConfig
from canadalogin_release.runtime import RuntimeContext

EXAMPLES = Path(__file__).parents[1] / "examples"


class RecordingRunner:
    def __init__(self, responses: Sequence[tuple[int, str]] = ()) -> None:
        self.commands: list[tuple[tuple[str, ...], Path, Mapping[str, str]]] = []
        self.unset_environments: list[tuple[str, ...]] = []
        self.responses = list(responses)

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
        self.commands.append((tuple(arguments), Path(cwd), environment or {}))
        self.unset_environments.append(tuple(unset_environment))
        return_code, stdout = self.responses.pop(0) if self.responses else (0, "")
        return subprocess.CompletedProcess(arguments, return_code, stdout, "")


class BuildTest(unittest.TestCase):
    def config(self, repository: str) -> PipelineConfig:
        return PipelineConfig.load(
            EXAMPLES / repository / "release-pipeline-configuration.yml"
        )

    def context(
        self,
        repository: Path,
        environment: str,
        *,
        release_tag: str | None = "v1.2.3",
        variables: Mapping[str, str] | None = None,
        secrets: Mapping[str, str] | None = None,
    ) -> RuntimeContext:
        return RuntimeContext.create(
            repository=repository,
            environment=environment,
            sha="abc123",
            release_tag=release_tag,
            github_ref="refs/heads/main",
            variables=variables,
            secrets=secrets,
            now=datetime(2026, 8, 12, 18, 30, tzinfo=UTC),
        )

    def test_command_build_resolves_environment_and_uploads_s3_artifact(self) -> None:
        config = self.config("gc-signin-partner-portal")
        runner = RecordingRunner()
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            (repository / "frontend" / "dist").mkdir(parents=True)
            result = execute_build(
                config,
                build_name="frontend",
                context=self.context(
                    repository,
                    "dev",
                    variables={
                        "VITE_APP_ENVIRONMENT": "development",
                    },
                    secrets={
                        "VITE_API_BASE_URL": "https://api.example",
                        "FRONTEND_APP_BUILD_ARTIFACTS_S3_BUCKET": "build-bucket",
                    },
                ),
                runner=runner,
            )

        self.assertEqual(result.release_version, "v1.2.3")
        self.assertEqual(runner.commands[0][0], ("corepack", "enable"))
        command_environment = runner.commands[0][2]
        self.assertEqual(
            command_environment["VITE_API_BASE_URL"], "https://api.example"
        )
        self.assertEqual(
            command_environment["VITE_AUTH_POST_LOGIN_PATH"], "/your-applications"
        )
        self.assertEqual(command_environment["VITE_RELEASE_TAG"], "v1.2.3")
        self.assertIn("BUILD_SECRET_1", runner.unset_environments[0])
        self.assertEqual(runner.commands[-1][0][-1], "--delete")
        self.assertIn("AWS_ACCESS_KEY_ID", runner.unset_environments[0])
        self.assertIn("GITHUB_TOKEN", runner.unset_environments[0])

    def test_docker_build_applies_sha_latest_release_and_build_args(self) -> None:
        config = self.config("gc-signin-user-selfservice-webapp")
        runner = RecordingRunner()
        with tempfile.TemporaryDirectory() as directory:
            result = execute_build(
                config,
                build_name="backend",
                context=self.context(
                    Path(directory),
                    "dev",
                    variables={"ARTIFACT_ECR_REPOSITORY": "example.dkr/repository"},
                ),
                runner=runner,
            )

        build_command = runner.commands[0][0]
        self.assertIn("RELEASE_TAG=v1.2.3", build_command)
        self.assertIn("BUILD_TIMESTAMP=2026-08-12T18:30:00Z", build_command)
        self.assertIn("example.dkr/repository:abc123", build_command)
        self.assertIn("example.dkr/repository:latest", build_command)
        self.assertIn("example.dkr/repository:v1.2.3", build_command)
        self.assertEqual(result.image_uri, "example.dkr/repository:abc123")
        self.assertEqual(len(runner.commands), 4)
        self.assertIn("AWS_SECRET_ACCESS_KEY", runner.unset_environments[0])
        self.assertIn("GITHUB_TOKEN", runner.unset_environments[0])

    def test_source_sha_controls_checkout_tags_and_result_identity(self) -> None:
        config = self.config("gc-signin-user-selfservice-webapp")
        runner = RecordingRunner()
        git_commands: list[tuple[tuple[str, ...], Path]] = []
        with tempfile.TemporaryDirectory() as directory:
            result = execute_build(
                config,
                build_name="load-test",
                context=self.context(
                    Path(directory),
                    "staging",
                    release_tag=None,
                    variables={"LOAD_TEST_ECR_REPOSITORY": "example.dkr/load-test"},
                ),
                source_sha="staging-sha",
                runner=runner,
                git_runner=lambda arguments, repository: (
                    git_commands.append((tuple(arguments), Path(repository))) or ""
                ),
                release_tag_resolver=lambda config, sha, repository: "v1.2.2",
            )

        self.assertEqual(git_commands[0][0], ("checkout", "--detach", "staging-sha"))
        self.assertIn("example.dkr/load-test:latest", runner.commands[0][0])
        self.assertEqual(result.source_sha, "staging-sha")
        self.assertEqual(result.release_tag, "v1.2.2")
        self.assertEqual(result.release_version, "v1.2.2")

    def test_non_development_command_build_reuses_existing_artifact(self) -> None:
        config = self.config("gc-signin-static-website")
        runner = RecordingRunner(responses=[(0, ""), (0, ""), (0, "object\n")])
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            (repository / "website" / "_site").mkdir(parents=True)
            execute_build(
                config,
                build_name="website",
                context=self.context(
                    repository,
                    "staging",
                    variables={
                        "GOOGLE_ANALYTICS_ID": "G-123",
                        "STATIC_WEBSITE_BUILD_ARTIFACTS_S3_BUCKET": "build-bucket",
                    },
                ),
                runner=runner,
            )

        self.assertEqual(runner.commands[-1][0][:3], ("aws", "s3", "ls"))
        self.assertFalse(
            any(command[0][:3] == ("aws", "s3", "sync") for command in runner.commands)
        )

    def test_empty_s3_prefix_is_not_reused(self) -> None:
        config = self.config("gc-signin-static-website")
        runner = RecordingRunner(responses=[(0, ""), (0, ""), (0, "")])
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            (repository / "website" / "_site").mkdir(parents=True)
            execute_build(
                config,
                build_name="website",
                context=self.context(
                    repository,
                    "staging",
                    variables={
                        "GOOGLE_ANALYTICS_ID": "G-123",
                        "STATIC_WEBSITE_BUILD_ARTIFACTS_S3_BUCKET": "build-bucket",
                    },
                ),
                runner=runner,
            )

        self.assertEqual(runner.commands[-1][0][:3], ("aws", "s3", "sync"))

    def test_rendered_empty_build_prefix_is_forbidden(self) -> None:
        config = self.config("gc-signin-static-website")
        artifact = replace(config.builds[0].s3_artifact, prefix="{release_tag}")
        build = replace(config.builds[0], s3_artifact=artifact)
        config = replace(config, builds=(build,))
        runner = RecordingRunner()
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            (repository / "website" / "_site").mkdir(parents=True)
            with self.assertRaisesRegex(ConfigError, "rendered empty"):
                execute_build(
                    config,
                    build_name="website",
                    context=self.context(
                        repository,
                        "dev",
                        release_tag=None,
                        variables={
                            "GOOGLE_ANALYTICS_ID": "G-123",
                            "STATIC_WEBSITE_BUILD_ARTIFACTS_S3_BUCKET": "build-bucket",
                        },
                    ),
                    runner=runner,
                )

        self.assertFalse(any(command[0][0] == "aws" for command in runner.commands))

    def test_existing_artifact_is_not_overwritten(self) -> None:
        config = self.config("gc-signin-static-website")
        runner = RecordingRunner(responses=[(0, ""), (0, ""), (0, "object\n")])
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            (repository / "website" / "_site").mkdir(parents=True)
            execute_build(
                config,
                build_name="website",
                context=self.context(
                    repository,
                    "dev",
                    variables={
                        "GOOGLE_ANALYTICS_ID": "G-123",
                        "STATIC_WEBSITE_BUILD_ARTIFACTS_S3_BUCKET": "build-bucket",
                    },
                ),
                runner=runner,
            )

        self.assertFalse(
            any(command[0][:3] == ("aws", "s3", "sync") for command in runner.commands)
        )

    def test_ecr_sha_build_requires_immutable_repository_and_records_digest(
        self,
    ) -> None:
        config = self.config("gc-signin-user-selfservice-webapp")
        runner = RecordingRunner(
            responses=[
                (
                    0,
                    json.dumps(
                        {
                            "repositories": [
                                {
                                    "imageTagMutability": "IMMUTABLE_WITH_EXCLUSION",
                                    "imageTagMutabilityExclusionFilters": [
                                        {"filterType": "WILDCARD", "filter": "latest"}
                                    ],
                                }
                            ]
                        }
                    ),
                ),
                (1, ""),
                (1, ""),
                (0, ""),
                (0, ""),
                (0, ""),
                (0, ""),
                (0, json.dumps({"imageDetails": [{"imageDigest": "sha256:abc"}]})),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            result = execute_build(
                config,
                build_name="backend",
                context=self.context(
                    Path(directory),
                    "dev",
                    variables={
                        "ARTIFACT_ECR_REPOSITORY": (
                            "123456789012.dkr.ecr.ca-central-1.amazonaws.com/app"
                        )
                    },
                ),
                runner=runner,
            )

        self.assertEqual(result.image_digest, "sha256:abc")
        self.assertIn("describe-repositories", runner.commands[0][0])

    def test_ecr_sha_build_reuses_existing_tag(self) -> None:
        config = self.config("gc-signin-user-selfservice-webapp")
        runner = RecordingRunner(
            responses=[
                (
                    0,
                    json.dumps(
                        {
                            "repositories": [
                                {
                                    "imageTagMutability": "IMMUTABLE_WITH_EXCLUSION",
                                    "imageTagMutabilityExclusionFilters": [
                                        {"filterType": "WILDCARD", "filter": "latest"}
                                    ],
                                }
                            ]
                        }
                    ),
                ),
                (0, json.dumps({"imageDetails": [{"imageDigest": "sha256:old"}]})),
                (0, json.dumps({"imageDetails": [{"imageDigest": "sha256:old"}]})),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            result = execute_build(
                config,
                build_name="backend",
                context=self.context(
                    Path(directory),
                    "dev",
                    variables={
                        "ARTIFACT_ECR_REPOSITORY": (
                            "123456789012.dkr.ecr.ca-central-1.amazonaws.com/app"
                        )
                    },
                ),
                runner=runner,
            )

        self.assertEqual(result.image_digest, "sha256:old")
        self.assertFalse(any(command[0][0] == "docker" for command in runner.commands))

    def test_ecr_existing_sha_rejects_missing_release_tag(self) -> None:
        config = self.config("gc-signin-user-selfservice-webapp")
        runner = RecordingRunner(
            responses=[
                (
                    0,
                    json.dumps(
                        {
                            "repositories": [
                                {
                                    "imageTagMutability": "IMMUTABLE_WITH_EXCLUSION",
                                    "imageTagMutabilityExclusionFilters": [
                                        {"filterType": "WILDCARD", "filter": "latest"}
                                    ],
                                }
                            ]
                        }
                    ),
                ),
                (0, json.dumps({"imageDetails": [{"imageDigest": "sha256:old"}]})),
                (1, ""),
            ]
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(ConfigError, "missing for existing SHA"),
        ):
            execute_build(
                config,
                build_name="backend",
                context=self.context(
                    Path(directory),
                    "dev",
                    variables={
                        "ARTIFACT_ECR_REPOSITORY": (
                            "123456789012.dkr.ecr.ca-central-1.amazonaws.com/app"
                        )
                    },
                ),
                runner=runner,
            )

        self.assertFalse(any(command[0][0] == "docker" for command in runner.commands))

    def test_ecr_existing_sha_rejects_divergent_release_tag(self) -> None:
        config = self.config("gc-signin-user-selfservice-webapp")
        runner = RecordingRunner(
            responses=[
                (
                    0,
                    json.dumps(
                        {
                            "repositories": [
                                {
                                    "imageTagMutability": "IMMUTABLE_WITH_EXCLUSION",
                                    "imageTagMutabilityExclusionFilters": [
                                        {"filterType": "WILDCARD", "filter": "latest"}
                                    ],
                                }
                            ]
                        }
                    ),
                ),
                (0, json.dumps({"imageDetails": [{"imageDigest": "sha256:old"}]})),
                (0, json.dumps({"imageDetails": [{"imageDigest": "sha256:new"}]})),
            ]
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(ConfigError, "expected 'sha256:old'"),
        ):
            execute_build(
                config,
                build_name="backend",
                context=self.context(
                    Path(directory),
                    "dev",
                    variables={
                        "ARTIFACT_ECR_REPOSITORY": (
                            "123456789012.dkr.ecr.ca-central-1.amazonaws.com/app"
                        )
                    },
                ),
                runner=runner,
            )

        self.assertFalse(any(command[0][0] == "docker" for command in runner.commands))


if __name__ == "__main__":
    unittest.main()
