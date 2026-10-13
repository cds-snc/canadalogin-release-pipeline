#!/usr/bin/env bash
set -euo pipefail

# Do not load caller-controlled trivy.yaml, ignore files, or ignore policies.
# Never install or execute dependencies from the source being scanned.
trivy filesystem \
  --config "$SCANNER_ACTION_PATH/trivy.yaml" \
  --ignorefile /dev/null \
  --ignore-policy '' \
  --scanners vuln \
  --pkg-types library \
  --severity CRITICAL \
  --exit-code 1 \
  --ignore-unfixed=false \
  --include-dev-deps \
  --detection-priority comprehensive \
  --file-patterns 'pip:(^|/)requirements([._-][^/]*)?\.(txt|lock)$' \
  --format table \
  --timeout 10m \
  "$SCAN_ROOT"