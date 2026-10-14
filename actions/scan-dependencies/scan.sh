#!/usr/bin/env bash
set -euo pipefail

# Only load the approved YAML ignore file, not caller configuration or ignore policies.
# Never install or execute dependencies from the source being scanned.
temporary_directory=$(mktemp -d)
trap 'rm -rf "$temporary_directory"' EXIT
ignore_file=/dev/null
if [[ -n "${POLICY_SHA:-}" ]]; then
  printf 'Exception policy SHA: %s\n' "$POLICY_SHA"
  policy_file=$(git -C "$SCAN_ROOT" ls-tree --name-only "$POLICY_SHA" -- .trivyignore.yaml)
  if [[ -n "$policy_file" ]]; then
    ignore_file="$temporary_directory/.trivyignore.yaml"
    git -C "$SCAN_ROOT" show "$POLICY_SHA:.trivyignore.yaml" > "$ignore_file"
  fi
fi

# A distinct finding exit code avoids confusing scanner errors with vulnerabilities.
scan_exit_code=0
trivy filesystem \
  --config "$SCANNER_ACTION_PATH/trivy.yaml" \
  --ignorefile "$ignore_file" \
  --ignore-policy '' \
  --scanners vuln \
  --pkg-types library \
  --severity CRITICAL \
  --exit-code 42 \
  --ignore-unfixed=false \
  --include-dev-deps \
  --detection-priority comprehensive \
  --file-patterns 'pip:(^|/)requirements([._-][^/]*)?\.(txt|lock)$' \
  --format table \
  --timeout 10m \
  "$SCAN_ROOT" > "$temporary_directory/report.txt" || scan_exit_code=$?

if [[ "$scan_exit_code" == 42 ]]; then
  cat <<'MESSAGE'
Deployment blocked: Trivy found CRITICAL dependency vulnerabilities. This vulnerability should be resolved by updating the dependency if possible. If an exception is needed, add an ignore rule to the repository-root .trivyignore.yaml and merge it into main before retrying. See [https://trivy.dev/docs/latest/configuration/filtering/#trivyignoreyaml](https://trivy.dev/docs/latest/configuration/filtering/#trivyignoreyaml).

CRITICAL vulnerabilities:
MESSAGE
  cat "$temporary_directory/report.txt"
  exit 1
fi
cat "$temporary_directory/report.txt"
exit "$scan_exit_code"