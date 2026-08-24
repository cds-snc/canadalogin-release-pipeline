#!/usr/bin/env bash
set -euo pipefail

repositories=()
buckets=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ecr-repository)
      repositories+=("$2")
      shift 2
      ;;
    --bucket)
      buckets+=("$2")
      shift 2
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

if [[ ${#repositories[@]} -eq 0 && ${#buckets[@]} -eq 0 ]]; then
  printf '%s\n' 'At least one ECR repository or bucket is required.' >&2
  exit 2
fi

for repository in "${repositories[@]}"; do
  image_digests="$(aws ecr list-images \
    --repository-name "$repository" \
    --filter tagStatus=ANY \
    --query 'imageIds[].imageDigest' \
    --output text)"
  while IFS= read -r digest; do
    [[ -z "$digest" || "$digest" == "None" ]] && continue
    aws ecr batch-delete-image \
      --repository-name "$repository" \
      --image-ids "imageDigest=$digest" \
      --output json >/dev/null
  done < <(printf '%s\n' "$image_digests" | tr '\t' '\n')
done

for bucket in "${buckets[@]}"; do
  aws s3 rm "s3://$bucket" --recursive
 done
