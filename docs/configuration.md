# Configuration reference

The default caller configuration path is `.github/release-pipeline-configuration.yml`. Unknown or malformed values fail before release-please, builds, or deployments begin.

## Top-level fields

| Field | Required | Description |
| --- | --- | --- |
| `schema_version` | yes | Must be `1`. |
| `application` | yes | Human-readable name used in notifications. |
| `aws_region` | no | AWS region, default `ca-central-1`. |
| `environments` | yes | Development, deployable, and versioned environments. |
| `release` | no | release-please and tag-prefix settings. |
| `notifications` | no | Slack webhook references and dev-failure policy. |
| `builds` | no | Artifact build definitions. |
| `deployments` | no | S3 or ECS deployment definitions. |
| `hooks` | no | Repository-owned lifecycle commands. |
| `events.repository_dispatch` | no | Dispatch event to environment mappings. |

## Value references

Infrastructure and build values use one of three reference forms:

```yaml
literal: fixed-value
from_variable:
  var: GITHUB_CONFIGURATION_VARIABLE
from_secret:
  secret: GITHUB_ACTIONS_SECRET
optional_variable:
  var: OPTIONAL_VARIABLE
  default: fallback
```

Variables come from `${{ toJSON(vars) }}` after the job declares its GitHub environment. Secrets are explicitly mapped into only the Python step that needs them.

Secret references are validated against the reusable workflow interface. Existing named application secrets remain supported. Custom build values may use `BUILD_SECRET_1` through `BUILD_SECRET_8`; custom S3/ECS values may use `DEPLOY_SECRET_1` through `DEPLOY_SECRET_8`; hooks use `HOOK_SECRET_1` through `HOOK_SECRET_4`. Configure those names in each GitHub environment and map them to meaningful command environment names in YAML.

Supported template fields are:

| Template | Value |
| --- | --- |
| `{sha}` | Desired deployment/build commit SHA. |
| `{environment}` | Selected GitHub environment. |
| `{release_tag}` | Semver tag on the workflow commit, or empty. |
| `{release_version}` | Release tag, otherwise `YYYYMMDD-<sha>`. |
| `{build_timestamp}` | UTC ISO-8601 build timestamp. |
| `{github_ref}` | Original workflow Git ref. |
| `{repository}` | Resolved Docker repository in Docker build arguments. |

ECS SSM parameter templates also support `{cluster}`, `{service}`, and `{container}`.

## Environments and release

```yaml
environments:
  development: dev
  deploy: [dev, test, staging, prod]
  versioned: [test, staging, prod]
  version_directory: .deployed_versions

release:
  enabled: true
  tag_prefix: v
```

The development environment always selects the workflow SHA. Each versioned environment reads `<version_directory>/<environment>.json` and resolves `tag_prefix + version` to a Git commit.

`versioned` may contain environments not yet listed in `deploy`. The partner portal uses this to retain its future test/staging/prod promotion files while currently deploying only dev.

When release support is enabled, repository validation requires:

- `.release-please-manifest.json`
- `release-please-config.json`
- the first versioned environment, normally test, in release-please `extra-files`
- every version file to contain only `{"version": "X.Y.Z"}`

## Notifications

```yaml
notifications:
  info_webhook:
    secret: GC_SIGNIN_OPS_SLACK_INFO_WEBHOOK
  alert_webhooks:
    - secret: GC_SIGNIN_OPS_SLACK_ALERT_WEBHOOK
    - secret: CL_DEV_SLACK_ALERT_WEBHOOK
  notify_development_failures: true
```

Start and success messages are sent for manifest promotions and manual deployments. Failure messages are sent for promoted/non-dev environments and, by default, dev. Every message names its environment.

## Command builds

```yaml
builds:
  - name: frontend
    kind: command
    environments: [dev, test, staging, prod]
    aws_role: github_action_push_S3
    node_version: "22"
    gates_deployment: true
    command:
      working_directory: frontend
      steps:
        - [npm, ci]
        - [npm, run, build]
      environment:
        VITE_API_URL:
          secret: VITE_API_BASE_URL
        VITE_ENVIRONMENT: "{environment}"
        VITE_RELEASE_TAG: "{release_version}"
    s3_artifact:
      source: frontend/dist
      bucket:
        secret: FRONTEND_APP_BUILD_ARTIFACTS_S3_BUCKET
      prefix: "{sha}"
      delete: true
      skip_if_exists_non_development: false
```

The artifact prefix is an immutable identity. The build checks it before upload and reuses an existing prefix rather than overwriting it. `skip_if_exists_non_development` is retained for compatibility with existing configurations; all environments now avoid overwriting an existing prefix.

Commands are arrays, not shell strings. Each command runs with Python `subprocess` and `shell=False`.

Build and source environments must be listed in `environments.deploy`. Repository-dispatch targets must also be deployable; typos are rejected while loading the configuration. Build and hook commands do not receive AWS or GitHub credentials.

## Docker builds

```yaml
builds:
  - name: backend
    kind: docker
    environments: [dev]
    aws_role: github_action_manage_push_ecr_ecs
    dns_audit: true
    shared_artifact: true
    docker:
      context: backend
      dockerfile: backend/Dockerfile
      repository:
        var: ARTIFACT_ECR_REPOSITORY
      tags: [sha, latest, release]
      build_args:
        APP_VERSION: "{release_version}"
    sbom:
      name: application-backend
      dockerfile: backend/Dockerfile
```

Allowed tag modes are:

- `sha`: `<repository>:<workflow-sha>`
- `latest`: `<repository>:latest`
- `release`: `<repository>:vX.Y.Z`, only when the commit has a release tag

When a Docker repository is an AWS ECR repository and the build publishes `sha` or `release`, the repository must use `IMMUTABLE` or `IMMUTABLE_WITH_EXCLUSION` tag mutability. SHA and release tags are checked before any push; an existing SHA image is reused only when every other non-excluded tag exists and resolves to the same digest. If `latest` is configured, it must be an explicit mutability exclusion. Successful ECR builds record the image digest in their workflow output.

Set `shared_artifact: true` when one build definition supplies every deployment environment, such as a backend image built with dev credentials. A manual non-dev rebuild then builds the selected environment's desired SHA instead of current main. Set `source_environment: staging` to always build staging's desired SHA, as used by load-test images. Non-gating source-environment builds run on every manual invocation to preserve the current load-test refresh behavior. The selected source SHA controls checkout, tags, build arguments, S3 prefixes, and release metadata. Set `gates_deployment: false` for an auxiliary artifact that must not block an otherwise valid application deployment.

The shared SBOM action submits dependency snapshots and therefore requires `contents: write`. A snapshot is generated only when the built source SHA equals the workflow SHA, which is the SHA the pinned action records. Rebuilding an older pinned environment skips a duplicate, incorrectly attributed snapshot; that source received its snapshot when it was originally built and released.

## S3 and CloudFront deployments

```yaml
deployments:
  - name: frontend
    kind: s3
    aws_role: github_action_push_S3
    artifact_bucket:
      secret: FRONTEND_APP_BUILD_ARTIFACTS_S3_BUCKET
    artifact_prefix: "{sha}"
    targets:
      - bucket:
          secret: FRONTEND_APP_S3_BUCKET
        delete: true
    invalidations:
      - distribution:
          secret: CLOUDFRONT_DISTRIBUTION_ID
        paths: [/index.html, /assets/*]
```

Any number of targets and invalidations may be declared. Every S3 artifact is checked for at least one object before the first target sync starts. An empty prefix is treated as missing, even when `aws s3 ls` exits successfully.

## ECS and SSM deployments

```yaml
deployments:
  - name: backend
    kind: ecs
    aws_role: github_action_manage_push_ecr_ecs
    repository:
      var: ARTIFACT_ECR_REPOSITORY
    services:
      - cluster:
          var: ECS_CLUSTER
        service:
          var: ECS_SERVICE
        container:
          var: ECS_CONTAINER
        ssm_parameter: /ecs/{cluster}/{service}/container-image
```

Multiple services may share one image, as in partner portal web and worker. All services of one kind in one environment must use the same role so the environment remains one job.

Before any S3 or ECS mutation, the environment workflow verifies all S3 artifacts and targets, CloudFront distributions, desired ECR image tags and digests, ECS services, task definitions, containers, and SSM parameter templates. Changed ECS task definitions use the verified ECR digest rather than a mutable tag. Structured ECS responses are consumed without writing them to workflow logs. ECS updates retain the current pipeline's `propagate-tags: SERVICE` behavior. Task-definition tags are not copied because the current deployment roles do not grant the additional tag read/write permissions.

After an ECS service update, the deployer polls `describe-services` every 15 seconds for up to 600 seconds. It fails immediately when ECS reports a failed rollout and includes rollout states, reasons, failed-task counts, service counts, and recent service events in the error. The SSM image pointer is updated only after the service is stable.

## Hooks

```yaml
hooks:
  before_deploy:
    - [python3, .github/release-hooks/preflight.py]
  health_check:
    - [python3, .github/release-hooks/health.py]
  after_deploy:
    - [python3, .github/release-hooks/announce.py]
  on_failure:
    - [python3, .github/release-hooks/collect-diagnostics.py]
```

Hooks run from the caller repository with:

- all GitHub configuration variables as ordinary environment variables
- `RELEASE_APPLICATION`
- `RELEASE_ENVIRONMENT`
- `RELEASE_DEPLOYMENT_SHA`
- `RELEASE_FORCE_REDEPLOY`
- optional environment secrets named `HOOK_SECRET_1` through `HOOK_SECRET_4`

Adding a new arbitrary secret requires mapping a `HOOK_SECRET_*` name in the caller environment. Secrets are not packed into a JSON object.

AWS access-key, web-identity, GitHub-token, and Actions runtime credential variables are removed before each configured hook starts. Hooks must use the documented hook secrets and variables; they cannot assume the deploy job's AWS session or GitHub token.

## Repository dispatch

```yaml
events:
  repository_dispatch:
    gc-articles-update: [dev]
```

Unknown repository dispatch event types default to dev. Pull requests only plan and comment; they never build or deploy. Pushes build configured artifacts and reconcile every enabled environment. Manual runs default to dev and rebuild only when `rebuild` is selected.