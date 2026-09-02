import os
import uuid

import boto3


client = boto3.client("dynamodb")


def handler(event, _context):
    client.put_item(
        TableName=os.environ["TABLE_NAME"],
        Item={
            "id": {"S": str(uuid.uuid4())},
            "body": {"S": event.get("body", "")},
        },
    )
    return {"statusCode": 204}