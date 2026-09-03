import os
import time
import uuid

MAX_BODY_BYTES = 16 * 1024
RETENTION_SECONDS = 30 * 60


def _client():
    import boto3

    return boto3.client("dynamodb")


def handler(event, _context):
    body = event.get("body", "")
    if not isinstance(body, str):
        return {"statusCode": 400}
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        return {"statusCode": 413}

    _client().put_item(
        TableName=os.environ["TABLE_NAME"],
        Item={
            "id": {"S": str(uuid.uuid4())},
            "body": {"S": body},
            "expires_at": {"N": str(int(time.time()) + RETENTION_SECONDS)},
        },
    )
    return {"statusCode": 204}