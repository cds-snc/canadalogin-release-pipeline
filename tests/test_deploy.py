from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from collections.abc import Mapping, Sequence
import hashlib
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from canadalogin_release.config import ConfigError, PipelineConfig
from canadalogin_release.pipeline.deploy import (
    ATTESTATION_SIGNER_IDENTITY_REGEX,
    ATTESTATION_SIGNER_REMOTE,
    ATTESTATION_SIGNER_WORKFLOW,
    _ecs_rollout_failed,
    _ecs_service_diagnostics,
    _ecs_service_is_stable,
    deploy_ecs,
    deploy_s3,
)
from canadalogin_release.support.runtime import RuntimeContext

FIXTURES = Path(__file__).parent / "fixtures"

MULTI_SERVICE_CONFIG = """
schema_version: 2
application: multi-service
profile: ecs-service
environments: [dev]
backend:
    dockerfile: backend/Dockerfile
    services: [web, worker]
"""


RELEASED_PIPELINE_COMMIT = "c664af9a7c15ab37dd3690a3bd86781b0acb89bf"
UNRELEASED_PIPELINE_COMMIT = "0902b1f918c7b76dbad22c964c3939eca685ce3e"
RELEASE_TAGS = (
    f"{UNRELEASED_PIPELINE_COMMIT}\trefs/tags/v1.2.4-rc1\n"
    f"{RELEASED_PIPELINE_COMMIT}\trefs/tags/v1.2.5\n"
)


def verified_attestations(*signer_digests: str) -> str:
    """Shape of `gh attestation verify --format json` output (sigstore-go VerificationResult)."""
    return json.dumps(
        [
            {
                "attestation": {"bundle": {}},
                "verificationResult": {
                    "mediaType": "application/vnd.dev.sigstore.verificationresult+json;version=0.1",
                    "signature": {
                        "certificate": {
                            "subjectAlternativeName": f"https://github.com/{ATTESTATION_SIGNER_WORKFLOW}@refs/tags/v1.2.5",
                            "buildSignerURI": f"https://github.com/{ATTESTATION_SIGNER_WORKFLOW}@refs/tags/v1.2.5",
                            "buildSignerDigest": digest,
                            "sourceRepositoryRef": "refs/heads/main",
                        }
                    },
                },
            }
            for digest in signer_digests
        ]
    )


class AwsRunner:
    def __init__(self, responses: Sequence[tuple[int, str]] = ()) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.unset_environments: list[tuple[str, ...]] = []
        self.log_outputs: list[bool] = []
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
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = tuple(arguments)
        self.commands.append(command)
        self.unset_environments.append(tuple(unset_environment))
        self.log_outputs.append(log_output)
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
            FIXTURES / repository / "release-pipeline-configuration.yml"
        )

    def config_from_text(self, content: str) -> PipelineConfig:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "release-pipeline-configuration.yml"
            path.write_text(content)
            return PipelineConfig.load(path)

    def context(
        self,
        repository: Path,
        variables: Mapping[str, str],
    ) -> RuntimeContext:
        return RuntimeContext.create(
            repository=repository,
            environment="dev",
            sha="abc123",
            release_tag=None,
            github_ref="refs/heads/main",
            variables=variables,
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
                                "desiredCount": 1,
                                "runningCount": 1,
                                "pendingCount": 0,
                                "rolloutState": "COMPLETED",
                            }
                        ],
                    }
                ],
                "failures": [],
            }
        )

    def test_s3_preflight_happens_before_sync_and_invalidation(self) -> None:
        config = self.config("canadalogin-static-website")
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

        self.assertEqual(
            runner.commands[0],
            ("aws", "s3", "ls", "s3://builds/dev/abc123/"),
        )
        self.assertNotIn("DEPLOY_SECRET_1", runner.unset_environments[0])
        self.assertEqual(
            runner.commands[5],
            (
                "aws",
                "s3",
                "sync",
                "s3://builds/dev/abc123",
                "s3://english",
                "--only-show-errors",
                "--delete",
            ),
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
        config = self.config("canadalogin-static-website")
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
        config = self.config("canadalogin-static-website")
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
        config = self.config("canadalogin-static-website")
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
        config = self.config("canadalogin-static-website")
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

        self.assertEqual(
            runner.commands,
            [("aws", "s3", "ls", "s3://builds/dev/abc123/")],
        )

    def test_ecs_same_image_is_a_no_op(self) -> None:
        config = self.config("canadalogin-user-selfservice-webapp")
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
        config = self.config("canadalogin-user-selfservice-webapp")
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

    def attestation_case(
        self,
        gh_return_code: int = 0,
        manifest: str = '{"schemaVersion":2}',
        gh_output: str | None = None,
        tags: str = RELEASE_TAGS,
        tags_return_code: int = 0,
        ecr_images: list[dict[str, object]] | None = None,
    ) -> tuple[PipelineConfig, RuntimeContext, AwsRunner, str]:
        config = self.config_from_text(
            MULTI_SERVICE_CONFIG.replace(
                "    services: [web, worker]\n",
                "    services: [web, worker]\n    verify_attestations: true\n",
            )
        )
        repository = "123456789012.dkr.ecr.ca-central-1.amazonaws.com/app"
        digest = "sha256:" + hashlib.sha256(b'{"schemaVersion":2}').hexdigest()
        image = json.dumps(
            {"imageDetails": [{"imageTags": ["abc123"], "imageDigest": digest}]}
        )
        if ecr_images is None:
            ecr_images = [{"imageManifest": manifest}]
        runner = AwsRunner(
            [
                (0, image),
                (0, json.dumps({"images": ecr_images, "failures": []})),
                (
                    gh_return_code,
                    verified_attestations(RELEASED_PIPELINE_COMMIT)
                    if gh_output is None
                    else gh_output,
                ),
                (tags_return_code, tags),
            ]
        )
        context = self.context(
            Path("."),
            {
                "RELEASE_ECR_REPOSITORY": repository,
                **{
                    f"RELEASE_ECS_{service}_{suffix}": f"{service.lower()}-{suffix.lower()}"
                    for service in ("WEB", "WORKER")
                    for suffix in ("CLUSTER", "SERVICE", "CONTAINER")
                },
            },
        )
        return config, context, runner, digest

    def run_attestation_case(self, **case: object) -> tuple[AwsRunner, str, ConfigError]:
        config, context, runner, digest = self.attestation_case(**case)  # type: ignore[arg-type]
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": "cds-snc/example"}):
            with self.assertRaises(ConfigError) as raised:
                # No ECS responses are queued, so a passed verification still ends in an
                # ECS error; the error message says which.
                deploy_ecs(config, context, runner=runner)
        return runner, digest, raised.exception

    def assert_verification_passed(self, runner: AwsRunner, error: ConfigError) -> None:
        self.assertEqual(runner.commands[4][0:3], ("aws", "ecs", "describe-services"))
        self.assertNotIn("attestation", str(error).lower())

    def test_attestation_verified_once_per_image_before_ecs_reads(self) -> None:
        runner, digest, error = self.run_attestation_case()

        self.assertEqual(runner.commands[1][0:3], ("aws", "ecr", "batch-get-image"))
        self.assertIn(f"imageDigest={digest}", runner.commands[1])
        gh = runner.commands[2]
        self.assertEqual(gh[0:3], ("gh", "attestation", "verify"))
        self.assertEqual(gh[gh.index("--repo") + 1], "cds-snc/example")
        self.assertNotIn("--signer-workflow", gh)
        self.assertEqual(
            gh[gh.index("--cert-identity-regex") + 1], ATTESTATION_SIGNER_IDENTITY_REGEX
        )
        self.assertEqual(gh[gh.index("--source-ref") + 1], "refs/heads/main")
        self.assertEqual(gh[gh.index("--source-digest") + 1], "abc123")
        self.assertEqual(
            gh[gh.index("--predicate-type") + 1], "https://slsa.dev/provenance/v1"
        )
        self.assertIn("--deny-self-hosted-runners", gh)
        self.assertEqual(gh[gh.index("--format") + 1], "json")
        self.assertEqual(
            runner.commands[3],
            ("git", "ls-remote", "--tags", ATTESTATION_SIGNER_REMOTE, "refs/tags/v*"),
        )
        self.assert_verification_passed(runner, error)
        self.assertEqual(
            sum(command[0:3] == ("gh", "attestation", "verify") for command in runner.commands),
            1,
        )

    def test_attestation_signer_identity_names_only_the_image_build_workflow(self) -> None:
        pattern = re.compile(ATTESTATION_SIGNER_IDENTITY_REGEX)
        workflow = f"https://github.com/{ATTESTATION_SIGNER_WORKFLOW}"

        self.assertTrue(ATTESTATION_SIGNER_WORKFLOW.endswith("/release__build-image.yml"))
        # The ref is not trusted (GitHub can name a tag-pinned call by SHA and vice versa);
        # the signer commit is checked against released tags instead.
        for ref in (
            "refs/tags/v1.2.4",
            RELEASED_PIPELINE_COMMIT,
            "refs/heads/main",
        ):
            with self.subTest(ref=ref):
                self.assertTrue(pattern.match(f"{workflow}@{ref}"))
        for identity in (
            f"{workflow}.yml@refs/tags/v1.2.4",
            f"{workflow}@",
            f"{workflow}@refs/tags/v1.2.4@x",
            workflow.replace("release__build-image.yml", "release__build-imageXyml")
            + "@refs/tags/v1.2.4",
            # Command builds run caller code with id-token: write: never a trusted signer.
            workflow.replace("release__build-image.yml", "release__build.yml")
            + "@refs/tags/v1.2.4",
            # Takes the digest as an input, so it would sign images built anywhere.
            workflow.replace("release__build-image.yml", "release__attest.yml")
            + "@refs/tags/v1.2.4",
            workflow.replace("cds-snc/", "cds-snc-fork/") + "@refs/tags/v1.2.4",
        ):
            with self.subTest(identity=identity):
                self.assertIsNone(pattern.match(identity))

    def test_signer_commit_must_be_a_released_tag(self) -> None:
        # RELEASE_TAGS points only a pre-release tag (v1.2.4-rc1) at this commit.
        runner, _, error = self.run_attestation_case(
            gh_output=verified_attestations(UNRELEASED_PIPELINE_COMMIT)
        )

        self.assertRegex(str(error), "none of which is a released vX.Y.Z tag")
        self.assertIn(UNRELEASED_PIPELINE_COMMIT, str(error))
        self.assertEqual(len(runner.commands), 4)

    def test_any_released_signer_among_verified_attestations_is_enough(self) -> None:
        runner, _, error = self.run_attestation_case(
            gh_output=verified_attestations(
                UNRELEASED_PIPELINE_COMMIT, RELEASED_PIPELINE_COMMIT.upper()
            )
        )

        self.assert_verification_passed(runner, error)

    def test_annotated_release_tags_are_peeled_to_their_commit(self) -> None:
        tag_object = "1" * 40
        runner, _, error = self.run_attestation_case(
            tags=(
                f"{tag_object}\trefs/tags/v1.2.5\n"
                f"{RELEASED_PIPELINE_COMMIT}\trefs/tags/v1.2.5^{{}}\n"
            )
        )
        self.assert_verification_passed(runner, error)

        runner, _, error = self.run_attestation_case(
            gh_output=verified_attestations(tag_object),
            tags=(
                f"{tag_object}\trefs/tags/v1.2.5\n"
                f"{RELEASED_PIPELINE_COMMIT}\trefs/tags/v1.2.5^{{}}\n"
            ),
        )
        self.assertRegex(str(error), "none of which is a released")

    def test_release_tag_lookup_failures_fail_closed(self) -> None:
        for name, case, message in (
            ("ls-remote fails", {"tags_return_code": 128}, "Unable to list release tags"),
            ("no release tags", {"tags": ""}, "No released vX.Y.Z tags"),
        ):
            with self.subTest(name):
                runner, _, error = self.run_attestation_case(**case)

                self.assertRegex(str(error), message)
                self.assertEqual(len(runner.commands), 4)

    def test_unreadable_gh_output_fails_closed(self) -> None:
        for output, message in (
            ("not json", "invalid JSON"),
            ("[]", "no signer commit"),
            (json.dumps([{"verificationResult": {"signature": {}}}]), "no signer commit"),
        ):
            with self.subTest(output=output):
                runner, _, error = self.run_attestation_case(gh_output=output)

                self.assertRegex(str(error), message)
                self.assertEqual(len(runner.commands), 3)

    def test_failed_attestation_verification_aborts_before_ecs(self) -> None:
        runner, _, error = self.run_attestation_case(gh_return_code=1, gh_output="")

        self.assertRegex(str(error), "attestation verification failed")
        self.assertEqual(len(runner.commands), 3)

    def test_manifest_digest_mismatch_aborts_before_gh(self) -> None:
        runner, _, error = self.run_attestation_case(manifest='{"tampered":1}')

        self.assertRegex(str(error), "refusing to verify")
        self.assertEqual(len(runner.commands), 2)

    def test_duplicate_ecr_entries_for_the_requested_digest_are_accepted(self) -> None:
        # ECR lists one entry per tag (here the SHA tag and latest) for the same digest.
        _, _, _, digest = self.attestation_case()
        manifest = '{"schemaVersion":2}'
        runner, _, error = self.run_attestation_case(
            ecr_images=[
                {"imageId": {"imageDigest": digest, "imageTag": "abc123"}, "imageManifest": manifest},
                {"imageId": {"imageDigest": digest, "imageTag": "latest"}, "imageManifest": manifest},
            ]
        )

        self.assertEqual(runner.commands[2][0:3], ("gh", "attestation", "verify"))
        self.assert_verification_passed(runner, error)

    def test_ecr_entries_that_disagree_or_are_missing_abort_before_gh(self) -> None:
        _, _, _, digest = self.attestation_case()
        manifest = '{"schemaVersion":2}'
        for name, images, message in (
            (
                "one entry tampered",
                [
                    {"imageId": {"imageDigest": digest, "imageTag": "abc123"}, "imageManifest": manifest},
                    {"imageId": {"imageDigest": digest, "imageTag": "latest"}, "imageManifest": '{"tampered":1}'},
                ],
                "refusing to verify",
            ),
            (
                "only another digest",
                [{"imageId": {"imageDigest": "sha256:" + "0" * 64}, "imageManifest": manifest}],
                "ECR returned no manifest",
            ),
            ("no entries", [], "ECR returned no manifest"),
        ):
            with self.subTest(name):
                runner, _, error = self.run_attestation_case(ecr_images=images)

                self.assertRegex(str(error), message)
                self.assertEqual(len(runner.commands), 2)

    def test_attestation_verification_is_off_by_default(self) -> None:
        config = self.config_from_text(MULTI_SERVICE_CONFIG)
        _, context, runner, _ = self.attestation_case()

        with self.assertRaises(ConfigError):
            deploy_ecs(config, context, runner=runner)

        self.assertFalse(
            any(
                command[0] in ("gh", "git") or "batch-get-image" in command
                for command in runner.commands
            )
        )

    def test_missing_ecr_image_aborts_before_service_mutation(self) -> None:
        config = self.config("canadalogin-user-selfservice-webapp")
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
        config = self.config("canadalogin-user-selfservice-webapp")
        ecs_deployment = next(
            deployment for deployment in config.deployments if deployment.kind == "ecs"
        )
        service_config = replace(
            ecs_deployment.services[0], ssm_parameter="/ecs/{unknown}"
        )
        deployment = replace(ecs_deployment, services=(service_config,))
        config = replace(
            config,
            deployments=tuple(
                deployment if item is ecs_deployment else item
                for item in config.deployments
            ),
        )
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
        config = self.config("canadalogin-user-selfservice-webapp")
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

        output = StringIO()
        with redirect_stdout(output):
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
        self.assertTrue(runner.log_outputs)
        self.assertTrue(all(not log_output for log_output in runner.log_outputs))
        self.assertIn("registering task definition", output.getvalue())
        self.assertIn("updating service", output.getvalue())
        self.assertIn("deployment complete", output.getvalue())
        self.assertNotIn('"taskDefinition"', output.getvalue())

    def test_ecs_deploy_updates_every_service_in_a_multi_service_backend(self) -> None:
        config = self.config_from_text(MULTI_SERVICE_CONFIG)
        repository = "123456789012.dkr.ecr.ca-central-1.amazonaws.com/partner"

        def service_document(task_definition: str, deployment_id: str) -> str:
            return json.dumps(
                {
                    "services": [
                        {
                            "taskDefinition": task_definition,
                            "deployments": [
                                {"id": deployment_id, "status": "PRIMARY"}
                            ],
                        }
                    ],
                    "failures": [],
                }
            )

        def task_document(image: str) -> str:
            return json.dumps(
                {
                    "taskDefinition": {
                        "family": "partner",
                        "containerDefinitions": [
                            {"name": "app", "image": image},
                        ],
                    }
                }
            )

        runner = AwsRunner(
            [
                (
                    0,
                    json.dumps(
                        {
                            "imageDetails": [
                                {
                                    "imageTags": ["abc123"],
                                    "imageDigest": "sha256:new",
                                }
                            ]
                        }
                    ),
                ),
                (0, service_document("task:web:1", "old-web")),
                (0, task_document(f"{repository}:old")),
                (0, service_document("task:worker:1", "old-worker")),
                (0, task_document(f"{repository}:old")),
                (0, json.dumps({"taskDefinition": {"taskDefinitionArn": "task:web:2"}})),
                (0, "{}"),
                (0, self.stable_service("task:web:2", "new-web")),
                (0, task_document(f"{repository}@sha256:new")),
                (0, "{}"),
                (
                    0,
                    json.dumps(
                        {"taskDefinition": {"taskDefinitionArn": "task:worker:2"}}
                    ),
                ),
                (0, "{}"),
                (0, self.stable_service("task:worker:2", "new-worker")),
                (0, task_document(f"{repository}@sha256:new")),
                (0, "{}"),
            ]
        )
        context = self.context(
            Path("."),
            {
                "RELEASE_ECR_REPOSITORY": repository,
                "RELEASE_ECS_WEB_CLUSTER": "cluster-web",
                "RELEASE_ECS_WEB_SERVICE": "service-web",
                "RELEASE_ECS_WEB_CONTAINER": "app",
                "RELEASE_ECS_WORKER_CLUSTER": "cluster-worker",
                "RELEASE_ECS_WORKER_SERVICE": "service-worker",
                "RELEASE_ECS_WORKER_CONTAINER": "app",
            },
        )

        result = deploy_ecs(config, context, runner=runner)

        self.assertEqual(
            result.changed_resources,
            (
                "ecs:cluster-web/service-web",
                "ecs:cluster-worker/service-worker",
            ),
        )
        update_commands = [
            command
            for command in runner.commands
            if command[:3] == ("aws", "ecs", "update-service")
        ]
        self.assertEqual(
            [command[command.index("--cluster") + 1] for command in update_commands],
            ["cluster-web", "cluster-worker"],
        )
        ssm_commands = [
            command
            for command in runner.commands
            if command[:3] == ("aws", "ssm", "put-parameter")
        ]
        self.assertEqual(
            [command[command.index("--name") + 1] for command in ssm_commands],
            [
                "/ecs/cluster-web/service-web/container-image",
                "/ecs/cluster-worker/service-worker/container-image",
            ],
        )

    def test_ecs_wait_failure_includes_rollout_diagnostics(self) -> None:
        config = self.config("canadalogin-user-selfservice-webapp")
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

    def test_ecs_stability_accepts_completed_primary_while_previous_drains(
        self,
    ) -> None:
        document = json.loads(self.stable_service("task:new", "deployment-new"))
        service = document["services"][0]
        service["runningCount"] = 2
        service["deployments"].append(
            {
                "id": "deployment-old",
                "status": "ACTIVE",
                "taskDefinition": "task:old",
                "desiredCount": 1,
                "runningCount": 1,
                "pendingCount": 0,
                "rolloutState": "COMPLETED",
            }
        )

        self.assertTrue(
            _ecs_service_is_stable(
                document,
                expected_task_definition_arn="task:new",
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

    def test_ecs_diagnostics_identify_other_deployment(self) -> None:
        document = {
            "services": [
                {
                    "runningCount": 2,
                    "desiredCount": 1,
                    "pendingCount": 1,
                    "deployments": [
                        {
                            "id": "deployment-new",
                            "status": "PRIMARY",
                            "taskDefinition": "task:new",
                            "desiredCount": 1,
                            "runningCount": 1,
                            "pendingCount": 0,
                            "rolloutState": "COMPLETED",
                        },
                        {
                            "id": "deployment-old",
                            "status": "ACTIVE",
                            "taskDefinition": "task:old",
                            "desiredCount": 1,
                            "runningCount": 1,
                            "pendingCount": 1,
                            "rolloutState": "IN_PROGRESS",
                        },
                    ],
                }
            ],
            "failures": [],
        }

        diagnostics = _ecs_service_diagnostics(
            document,
            expected_task_definition_arn="task:new",
        )

        self.assertIn("expected deployment is ready", diagnostics)
        self.assertIn("waiting on other deployment(s)", diagnostics)
        self.assertIn("id=deployment-old", diagnostics)
        self.assertIn("task_definition=task:old", diagnostics)

    def test_ecs_wait_timeout_is_bounded_and_includes_last_diagnostics(self) -> None:
        config = self.config("canadalogin-user-selfservice-webapp")
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
                                "taskDefinition": "task:2",
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

        output = StringIO()
        with (
            patch(
                "canadalogin_release.pipeline.deploy.time.monotonic",
                side_effect=(0, 601),
            ),
            redirect_stdout(output),
            self.assertRaisesRegex(
                ConfigError,
                "within 600 seconds.*rollout_state.*IN_PROGRESS.*waiting for task",
            ),
        ):
            deploy_ecs(config, context, runner=runner)

        self.assertIn(
            "expected deployment is still starting or reaching capacity",
            output.getvalue(),
        )
        self.assertFalse(
            any(command[1:3] == ("ssm", "put-parameter") for command in runner.commands)
        )

    def test_force_redeploy_uses_current_task_definition(self) -> None:
        config = self.config("canadalogin-user-selfservice-webapp")
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
