from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

MODULE_PATH = (
    Path(__file__).parents[1]
    / "acceptance"
    / "support"
    / "terraform"
    / "notification-capture"
    / "index.py"
)


def load_notification_capture():
    specification = importlib.util.spec_from_file_location(
        "notification_capture", MODULE_PATH
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("Unable to load notification capture Lambda")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class NotificationCaptureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_notification_capture()

    def test_stores_body_with_thirty_minute_expiry(self) -> None:
        client = Mock()
        with (
            patch.dict(os.environ, {"TABLE_NAME": "notifications"}, clear=True),
            patch.object(self.module, "_client", return_value=client),
            patch.object(self.module.time, "time", return_value=1_000),
        ):
            response = self.module.handler({"body": "release failed"}, None)

        self.assertEqual(response, {"statusCode": 204})
        client.put_item.assert_called_once_with(
            TableName="notifications",
            Item={
                "id": {"S": unittest.mock.ANY},
                "body": {"S": "release failed"},
                "expires_at": {"N": "2800"},
            },
        )

    def test_rejects_oversized_body_without_storing_it(self) -> None:
        client = Mock()
        with patch.object(self.module, "_client", return_value=client):
            response = self.module.handler(
                {"body": "x" * (self.module.MAX_BODY_BYTES + 1)}, None
            )

        self.assertEqual(response, {"statusCode": 413})
        client.put_item.assert_not_called()

    def test_rejects_non_string_body_without_storing_it(self) -> None:
        client = Mock()
        with patch.object(self.module, "_client", return_value=client):
            response = self.module.handler({"body": {"text": "invalid"}}, None)

        self.assertEqual(response, {"statusCode": 400})
        client.put_item.assert_not_called()