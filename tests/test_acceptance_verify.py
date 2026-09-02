import os
import unittest
from unittest.mock import patch

from acceptance.support.verify import VerificationContext, verify_react_site


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