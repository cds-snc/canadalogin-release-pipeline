# Current pipeline inventory

The five workflows were inspected from current `main` on 2026-08-12. Their shared shape is release-please, build on each main push, resolve desired versions, deploy to AWS, and send Slack notifications. Their implementation details have diverged substantially.

## Repository comparison

| Repository | Deployed environments | Build shape | Deployment shape | Notable customization |
| --- | --- | --- | --- | --- |
| `gc-signin-user-selfservice-webapp` | dev, test, staging, prod | Four environment-specific Vite builds; one backend image; staging load-test image | One S3/CloudFront frontend and one ECS backend | Release/date version metadata, SHA/latest/semver image tags, two alert channels, load-test SBOM path |
| `gc-signin-static-website` | dev, test, staging, prod | Four Eleventy builds | One artifact copied to English and French buckets; two CloudFront distributions | `gc-articles-update` repository dispatch, dev artifact overwrite, non-dev artifact reuse, release-please advances all version files |
| `gc-signin-partner-portal` | dev currently enabled | One pnpm/Vite build; one backend image | One S3/CloudFront frontend and two ECS services, web and worker | Node 22, pnpm 9.15.4, build defaults, separate container names, SHA/latest/semver tags |
| `gc-sign-in-migration` | dev, test, staging, prod | Four Vite builds; one backend image; staging load-test image | One S3/CloudFront frontend and one ECS backend | Backend publishes only SHA tag; load-test image; repository variables hold frontend values |
| `gc-signin-migration-oidc-rp-simulator` | dev, test, staging | One backend image | One ECS service | No production environment, SHA/latest tags, older action pins in the copied workflow |

## Complete required feature set

### Release and versioning

- Run release-please on main pushes using `CDS_RELEASE_BOT_APP_ID` and `CDS_RELEASE_BOT_PRIVATE_KEY`.
- Continue using caller-owned `release-please-config.json` and `.release-please-manifest.json`.
- Continue updating `.deployed_versions/test.json` from release-please where configured.
- Allow repositories such as the static website to advance additional environment files from release-please.
- Treat the main commit as the desired dev version.
- Treat `.deployed_versions/<environment>.json` as the desired non-dev semantic version.
- Resolve a non-dev version through the repository's `v<version>` Git tag.
- Detect a release tag on the current commit for application and image metadata.
- Fall back to `YYYYMMDD-<sha>` build metadata when no release tag exists.

### Builds

- Run commands in an application-selected working directory.
- Select Node.js versions, npm or pnpm commands, and environment-specific build values.
- Read infrastructure from either GitHub environment variables or environment secrets.
- Upload environment-specific frontend/static artifacts to S3 under the commit SHA.
- Optionally skip an existing non-dev artifact while allowing dev to overwrite it.
- Build Docker contexts with configurable Dockerfiles and build arguments.
- Push immutable SHA tags, optional `latest` tags, and optional semver tags.
- Run the CDS DNS audit action for backend image builds.
- Generate SBOMs with caller-selected names and Dockerfiles.
- Build a load-test image from the version currently desired in staging.
- Keep auxiliary builds visible without allowing them to create a partial application deployment.

### Deployments

- Select any configured subset of environments, not a hardcoded four-environment list.
- Check that an S3 build artifact exists before changing an application bucket.
- Sync to one or many S3 buckets, with configurable `--delete` behavior.
- Invalidate one or many CloudFront distributions with configurable path sets.
- Deploy one or many ECS services from one image.
- Resolve cluster, service, and container independently for each ECS service.
- Skip an ECS update when the desired SHA is already current.
- Force a same-version ECS deployment from a manual workflow.
- Preserve the existing task definition except for the selected container image.
- Wait for ECS service stability before declaring success.
- Update `/ecs/<cluster>/<service>/container-image` only after stability succeeds.
- Support separate S3 and ECS IAM roles within one environment deployment.

### Communication and controls

- Send Slack start and success messages for promoted or manually selected environments.
- Send failure alerts that always name the environment and failed build/deployment.
- Support the website-specific Slack webhook names and the additional dev alert channel.
- Comment on a pull request when a version file changes, including old and desired versions.
- Update or remove that comment when the pull request changes.
- Preserve GitHub environment variable and secret lookup from nested reusable workflows.
- Serialize release runs and environment mutations.
- Retain CODEOWNERS or ruleset-based approval controls for version files.
- Allow trusted repository scripts at defined lifecycle points.

## Problems found in the copied workflows

1. Frontend and backend build/deploy matrices are independent. A frontend build failure can coexist with successful backend deployments.
2. Matrix `fail-fast` defaults to true. One failed environment can cancel sibling jobs after other component jobs have already changed infrastructure.
3. Most repositories do not serialize release runs. Two pushes can race on the same environment.
4. Slack only derives promoted environments from version-file changes. Dev failures can be reported without saying dev, or not reported as deployments at all.
5. There is no supported same-version force redeploy path for ECS.
6. Promotion impact is easy to miss in a large pull request.
7. Bash repeats version parsing, tag resolution, AWS discovery, JSON construction, and notification logic without tests.
8. Action versions and quoting practices have drifted between copies.
9. `gc-sign-in-migration/.github/CODEOWNERS` owns `.deployed_versions/production.json`, but the real file is `.deployed_versions/prod.json`.
10. Static website and partner portal do not have deployment-file CODEOWNERS. Static website currently compensates with a custom two-review ruleset; partner portal needs an explicit deployment approval decision before non-dev rollout.
11. The incident pull requests cited in `gc-signin-sre#179` were merged while GitHub still reported `REVIEW_REQUIRED`, showing that a visible owner rule is not sufficient unless the active ruleset enforces it.

The shared implementation addresses items 1 through 8. Items 9 through 11 are repository rollout tasks because review enforcement lives in each caller repository's rulesets.