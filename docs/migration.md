# Migration and rollout

## Publish the shared repository

1. Create `cds-snc/canadalogin-release-system` from this local repository.
2. Choose public visibility, or configure a private repository's Actions access policy to allow every caller repository.
3. Confirm organization Actions policy permits this repository's workflows and actions.
4. Run CI and create a protected `v1.0.5` release tag. Never move a published release tag.
5. Protect `.github/workflows`, `actions`, and `src` with CODEOWNERS and required reviews.
6. Use release-please or an equivalent reviewed process for later shared-system versions.

## Migrate a caller

1. Copy the closest file under `examples/<repository>/release-pipeline.toml` to `.github/release-pipeline.toml`.
2. Copy [the caller workflow](../examples/caller/release-pipeline.yml) to `.github/workflows/release-pipeline.yml`. Use the static-site variant when `gc-articles-update` is required.
3. Pin the caller to a reviewed, protected `v1.0.5` release tag or full commit SHA. A full SHA is GitHub's immutable form; do not use `main` in a production caller.
4. Keep `.release-please-manifest.json`, `release-please-config.json`, and `.deployed_versions` unchanged.
5. Keep existing GitHub environment variables and secrets. The examples deliberately preserve their current names and whether each value is a variable or secret.
6. Ensure the caller grants the reusable workflow's required permission union: `contents: write`, `issues: write`, `pull-requests: write`, and `id-token: write`. The shared workflow narrows these permissions per job, and ordinary build commands run with `contents: read` and without AWS or GitHub credentials.
7. Ensure the caller uses `secrets: inherit`.
8. Add the caller workflow and configuration to CODEOWNERS.

## Approval prerequisites

The workflow cannot repair a repository ruleset after a deployment PR is merged. Before enabling non-dev deployment, verify:

- the active main ruleset requires pull requests
- code-owner review or a path-specific required reviewer rule is enforced for `.deployed_versions/**`
- stale reviews and last-push approval behavior match the team's policy
- bypass permissions are intentionally limited
- workflow changes themselves require owner review

Known repository follow-ups:

| Repository | Required follow-up |
| --- | --- |
| `gc-sign-in-migration` | Change CODEOWNERS from `.deployed_versions/production.json` to `.deployed_versions/prod.json`. |
| `gc-signin-static-website` | Retain and verify the active path-specific two-review ruleset; the local validator will warn because this policy is not visible in CODEOWNERS. |
| `gc-signin-partner-portal` | Define the intended deployment-file reviewer policy before enabling test, staging, or prod. |

## Staged validation

1. Open a pull request that changes no version file. Confirm no deployment-impact comment remains.
2. Change `test.json` in a pull request. Confirm the comment shows current and desired versions and required reviewers are requested.
3. Merge a normal code change. Confirm release-please runs, every required artifact builds, and dev deploys only after builds pass.
4. Confirm pinned non-dev environments reconcile without changing versions.
5. Promote test, staging, and prod one at a time. Confirm Slack names the environment and SSM changes after ECS stability.
6. Run a manual same-version deployment with `force-redeploy: true` and `rebuild: false`.
7. Trigger an intentional build failure in a non-production test. Confirm no environment deploy starts and the Slack alert names the build and environment.
8. Trigger an intentional health-check failure. Confirm the failure hook and environment-specific Slack alert run.

Start with the static website or RP simulator to validate one deployment primitive, then migrate migration/manage, and migrate partner portal after its non-dev environments and approval policy are ready.

## Version updates

Shared releases should use semantic version tags. A caller update is a one-line change to its reusable-workflow ref. Use Dependabot for GitHub Actions updates where possible, and review release notes before changing major versions.

The shared workflow also serializes pipeline runs per caller repository. Keep the caller-level `concurrency` block from the example because it protects the caller's other release jobs and makes the intended queue behavior visible.

For emergency fixes, publish a patch release and update callers. Do not move an existing release tag.

## Rollback during initial rollout

The first version intentionally preserves current deployment mechanisms. Until transactional rollback is implemented:

- revert desired versions through a reviewed pull request
- use the manual force-redeploy path for cache or ECS refresh issues
- use existing AWS operational procedures for an interrupted S3/ECS deployment
- treat a failed health hook as an alert and stop signal, not an automatic rollback

The architecture records enough pre-state to add coordinated rollback without replacing the caller contract. See [rollback-roadmap.md](rollback-roadmap.md).