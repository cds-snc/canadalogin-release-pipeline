import os
import unittest
from unittest.mock import patch

from acceptance.support.verify import (
    VerificationContext,
    VerificationError,
    release_image_exists,
    verify_failure_alert,
    verify_react_site,
)


class AcceptanceVerificationTests(unittest.TestCase):
    def test_context_reads_acceptance_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ACCEPTANCE_ENVIRONMENT": "dev",
                "REQUIRED_BUILDS_RESULT": "failure",
                "DEPLOY_RESULT": "skipped",
            },
            clear=True,
        ):
            context = VerificationContext.from_environment()

        self.assertEqual(context.environment, "dev")
        self.assertEqual(context.required_builds_result, "failure")
        self.assertEqual(context.deploy_result, "skipped")

    @patch(
        "acceptance.support.verify.aws_json",
        side_effect=VerificationError("ImageNotFoundException"),
    )
    def test_release_image_exists_returns_false_when_ecr_image_is_missing(
        self, aws_json
    ) -> None:
        self.assertFalse(release_image_exists({"ecr_repository": "app"}, "a" * 40))

        aws_json.assert_called_once_with(
            "ecr",
            "describe-images",
            "--repository-name",
            "app",
            "--image-ids",
            f"imageTag={'a' * 40}",
        )

    @patch(
        "acceptance.support.verify.aws_json",
        side_effect=VerificationError("AccessDeniedException"),
    )
    def test_release_image_exists_propagates_other_ecr_errors(
        self, aws_json
    ) -> None:
        with self.assertRaisesRegex(VerificationError, "AccessDeniedException"):
            release_image_exists({"ecr_repository": "app"}, "a" * 40)

        aws_json.assert_called_once()

    @patch(
        "acceptance.support.verify.aws_json",
        return_value={
            "Items": [
                {
                    "body": {
                        "S": (
                            ":x: release pipeline acceptance deployment failure "
                            "failed to deploy to `acceptance-deploy-failure`.\n\n"
                            "<https://github.com/cds-snc/canadalogin-release-pipeline/"
                            "actions/runs/123|View the release pipeline run>"
                        )
                    }
                }
            ]
        },
    )
    def test_verify_failure_alert_accepts_deploy_notification_text(
        self, aws_json
    ) -> None:
        context = VerificationContext(
            resources={"notification_capture_table": "notifications"},
            release_sha="a" * 40,
            account_id="429694360874",
            region="ca-central-1",
            environment="acceptance-deploy-failure",
            expected_result="failure",
            pipeline_result="failure",
            required_builds_result="success",
            deploy_result="failure",
            repository="cds-snc/canadalogin-release-pipeline",
            run_id="123",
        )

        verify_failure_alert(context, "deploy")

        aws_json.assert_called_once_with(
            "dynamodb", "scan", "--table-name", "notifications"
        )

    @patch("acceptance.support.verify.command", return_value="a" * 40)
    @patch(
        "acceptance.support.verify.aws_json",
        return_value={"Contents": [{"Key": "dev/" + "a" * 40 + "/index.html"}]},
    )
    def test_react_site_uses_environment_qualified_artifact_prefix(
        self, aws_json, command
    ) -> None:
        release_sha = "a" * 40

        verify_react_site(
            {
                "artifact_bucket": "artifacts",
                "site_bucket": "site",
            },
            release_sha,
            "dev",
        )

        aws_json.assert_called_once_with(
            "s3api",
            "list-objects-v2",
            "--bucket",
            "artifacts",
            "--prefix",
            f"dev/{release_sha}",
        )
        command.assert_called_once()


if __name__ == "__main__":
    unittest.main()