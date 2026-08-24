#!/usr/bin/env bash
set -euo pipefail

: "${AWS_REGION:?AWS_REGION is required}"
: "${TF_STATE_BUCKET:?TF_STATE_BUCKET is required}"
: "${TF_STATE_LOCK_TABLE:?TF_STATE_LOCK_TABLE is required}"

export AWS_DEFAULT_REGION="$AWS_REGION"

if ! aws s3api head-bucket --bucket "$TF_STATE_BUCKET" >/dev/null 2>&1; then
  if [[ "$AWS_REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$TF_STATE_BUCKET"
  else
    aws s3api create-bucket \
      --bucket "$TF_STATE_BUCKET" \
      --create-bucket-configuration "LocationConstraint=$AWS_REGION"
  fi
fi

aws s3api put-bucket-versioning \
  --bucket "$TF_STATE_BUCKET" \
  --versioning-configuration Status=Enabled
aws s3api put-public-access-block \
  --bucket "$TF_STATE_BUCKET" \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption \
  --bucket "$TF_STATE_BUCKET" \
  --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

if ! aws dynamodb describe-table --table-name "$TF_STATE_LOCK_TABLE" >/dev/null 2>&1; then
  aws dynamodb create-table \
    --table-name "$TF_STATE_LOCK_TABLE" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST >/dev/null
  aws dynamodb wait table-exists --table-name "$TF_STATE_LOCK_TABLE"
fi
