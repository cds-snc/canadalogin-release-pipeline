# Configuration reference

The default caller configuration path is `.github/release-pipeline-configuration.yml`. Unknown or malformed values fail before release-please, builds, or deployments begin.

## Configuration schema

The only currently supported schema is `schema_version: 2`. It describes the application profile and only the values that are genuinely application-specific. Release-please, environment promotion, artifact identity, AWS region, concurrency, approvals, notifications, and deployment safety remain central behavior.

```yaml
schema_version: 2
application: profile-management
profile: spa-ecs
environments: [dev, test, staging, prod]

frontend:
  environment:
    VITE_BACKEND_API_URL:
      var: VITE_BACKEND_API_URL
    VITE_ENVIRONMENT: "{environment}"
    VITE_RELEASE_TAG: "{release_version}"
  invalidation_paths: [/index.html, /assets/*]

backend:
  dockerfile: backend/Dockerfile

load_tests:
  enabled: true
```

The supported profiles are:

- `ecs-service`: one Dockerfile-backed ECS image and service.
- `spa-ecs`: one frontend build published to S3 and one shared Dockerfile-backed ECS image.
- `static-site`: one static build published to the declared S3 targets, with one target and CloudFront invalidation per site language or domain.

Backend blocks accept `dockerfile` only. Docker build context is inferred from the Dockerfile's parent directory, so `backend/Dockerfile` uses `backend` as its context. The schema rejects the old `context` key.

Frontend and static-site installs use `npm ci` with the repository lockfile. The pnpm exception uses the declared pnpm version and `--frozen-lockfile`. Node.js defaults to `22`.

### Infrastructure contract

Resource names are supplied as GitHub **Variables** on each deployment environment. They are not derived from the display name in `application` and are not secrets. Terraform must publish or maintain these keys as part of the application's release contract:

| Variable | Used by |
| --- | --- |
| `AWS_ACCOUNT_ID` | Central AWS role ARN construction. |
| `RELEASE_S3_ROLE` | S3 and CloudFront build/deploy role name. |
| `RELEASE_ECS_ROLE` | ECR, ECS, and SSM build/deploy role name. |
| `RELEASE_FRONTEND_ARTIFACT_BUCKET` | SPA frontend build artifact bucket. |
| `RELEASE_FRONTEND_BUCKET` | SPA frontend deployment bucket. |
| `RELEASE_FRONTEND_DISTRIBUTION_ID` | SPA frontend CloudFront distribution. |
| `RELEASE_STATIC_ARTIFACT_BUCKET` | Static-site build artifact bucket. |
| `RELEASE_SITE_<TARGET>_BUCKET` | Static-site target bucket, such as `RELEASE_SITE_EN_BUCKET`. |
| `RELEASE_SITE_<TARGET>_DISTRIBUTION_ID` | Static-site target distribution. |
| `RELEASE_ECR_REPOSITORY` | Standard backend ECR repository URL. |
| `RELEASE_LOAD_TEST_ECR_REPOSITORY` | Optional load-test ECR repository URL. |
| `RELEASE_ECS_CLUSTER` | Default backend ECS cluster name. |
| `RELEASE_ECS_SERVICE` | Default backend ECS service name. |
| `RELEASE_ECS_CONTAINER` | Default backend ECS container name. |
| `RELEASE_ECS_<SERVICE>_CLUSTER` | Named ECS service cluster, for example `WEB`. |
| `RELEASE_ECS_<SERVICE>_SERVICE` | Named ECS service name. |
| `RELEASE_ECS_<SERVICE>_CONTAINER` | Named ECS container name. |

Terraform outputs should be mapped to these stable keys for every enabled environment. A multi-service application publishes one key set per service; a bilingual site publishes one key set per target. Resource values are passed to the release CLI through `RELEASE_PIPELINE_VARS` and are never placed in the application YAML.

### Load tests

```yaml
load_tests:
  enabled: true
  dockerfile: load_tests/Dockerfile
```

`enabled` defaults to the standard `load_tests/Dockerfile`. The required build uses the staging desired SHA and publishes the SHA and `latest` image tags to `RELEASE_LOAD_TEST_ECR_REPOSITORY`. It does not update an application ECS service. The source Dockerfile must exist in the caller repository before enabling the capability.

## Top-level fields

| Field | Required | Description |
| --- | --- | --- |
| `schema_version` | yes | Must be `2`. |
| `application` | yes | Human-readable name used in notifications. |
| `profile` | yes | Application shape: `ecs-service`, `spa-ecs`, or `static-site`. |
| `environments` | no | Deployable environments; defaults to `dev`, `test`, `staging`, and `prod`. |
| `frontend` | required for `spa-ecs` | Frontend directory, build environment, and CloudFront paths. |
| `backend` | required for `ecs-service` and `spa-ecs` | Backend Dockerfile, build arguments, and optional service names. |
| `site` | required for `static-site` | Static-site environment and S3 target definitions. |
| `load_tests` | no | Optional staging load-test image. |
| `hooks` | no | Repository-owned lifecycle commands. |
| `events.repository_dispatch` | no | Dispatch event to environment mappings; targets must be deployable. |

## Value references

Infrastructure and build values use one of three reference forms:

```yaml
value: fixed-value
var: GITHUB_CONFIGURATION_VARIABLE
secret: GITHUB_ACTIONS_SECRET
var: OPTIONAL_VARIABLE
  default: fallback
```

Variables come from the release workflow's `RELEASE_PIPELINE_VARS` object after the job declares its GitHub environment. Secrets are explicitly mapped into only the Python step that needs them. Frontend `VITE_*` values are normally Variables because they are embedded in browser-visible assets. The standard Slack secrets described below are used instead of Slack value references.

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
environments: [dev, test, staging, prod]
```

The `dev` environment always selects the workflow SHA. Every other environment reads `.deployed_versions/<environment>.json` and resolves the version with the standard `v` tag prefix.

Repository validation requires:

- `.release-please-manifest.json`
- `release-please-config.json`
- the first versioned environment, normally test, in release-please `extra-files`
- every version file to contain only `{"version": "X.Y.Z"}`

## Notifications

Configurations contain no Slack fields. Register these as optional
GitHub Actions **secrets** on each deployment environment:

| Secret | Behavior |
| --- | --- |
| `RELEASE_PIPELINE_DEPLOY_INFO_SLACK_WEBHOOK` | The single info destination when no numbered info secret is registered. |
| `RELEASE_PIPELINE_DEPLOY_INFO_SLACK_WEBHOOK_1` through `_5` | Up to five info destinations. If any numbered secret is registered, all non-empty numbered secrets are used in numerical order and the unsuffixed secret is ignored. |
| `RELEASE_PIPELINE_DEPLOY_ALERTS_SLACK_WEBHOOK` | The single alert destination when no numbered alert secret is registered. |
| `RELEASE_PIPELINE_DEPLOY_ALERTS_SLACK_WEBHOOK_1` through `_5` | Up to five alert destinations. If any numbered secret is registered, all non-empty numbered secrets are used in numerical order and the unsuffixed secret is ignored. |

INFO and ALERT use exactly the same resolution structure: a single unsuffixed
secret for one destination, or numbered `_1` through `_5` secrets for multiple
destinations. Numbered slots may be sparse; missing or empty slots are skipped.
Secrets ending in `_6` or higher are not supported. Info messages cover
deployment start and success. Alert messages cover build, SBOM, deployment, and
pipeline failures. The reusable workflows map these secrets directly to their
notification steps, so applications do not declare Slack settings in YAML.

Start and success messages are sent for manifest promotions and manual deployments. Failure messages are sent for promoted/non-dev environments and, by default, dev. Every message names its environment.

## Frontend and static-site builds

For `spa-ecs`, the frontend directory defaults to `frontend`, the output
directory defaults to `dist`, and the build runs `npm ci` followed by `npm run
build`. Set `package_manager: pnpm` to use the pinned pnpm toolchain instead.
Frontend values are declared under `frontend.environment`; the generated S3
artifact is identified by the commit SHA and is never overwritten.

```yaml
frontend:
  directory: frontend
  environment:
    VITE_API_URL:
      var: VITE_API_URL
    VITE_ENVIRONMENT: "{environment}"
  delete_stale_files: true
  invalidation_paths: [/index.html, /assets/*]
```

For `static-site`, the `site.targets` mapping declares one bucket and optional
CloudFront invalidation paths per target. The build output defaults to
`website/_site` and is stored in `RELEASE_STATIC_ARTIFACT_BUCKET`.

Commands are argv arrays inside the implementation and run with Python
`subprocess` and `shell=False`. Build and hook commands do not receive AWS or
GitHub credentials.

## Docker builds

```yaml
schema_version: 2
application: example-service
profile: ecs-service
environments: [dev, test, staging, prod]

backend:
  dockerfile: backend/Dockerfile
  build_args:
    APP_VERSION: "{release_version}"
```

Allowed tag modes are:

- `sha`: `<repository>:<workflow-sha>`
- `latest`: `<repository>:latest`
- `release`: `<repository>:vX.Y.Z`, only when the commit has a release tag

When a Docker repository is an AWS ECR repository and the build publishes `sha` or `release`, the repository must use `IMMUTABLE` or `IMMUTABLE_WITH_EXCLUSION` tag mutability. SHA and release tags are checked before any push; an existing SHA image is reused only when every other non-excluded tag exists and resolves to the same digest. If `latest` is configured, it must be an explicit mutability exclusion. Successful ECR builds record the image digest in their workflow output.

The backend image is built once with the development environment's contract and
is reused by every deployment environment. An enabled `load_tests` block adds a
required staging image using `RELEASE_LOAD_TEST_ECR_REPOSITORY`; it does not
update an application ECS service. The selected source SHA controls checkout,
tags, build arguments, S3 prefixes, and release metadata.

The shared SBOM action submits dependency snapshots and therefore requires `contents: write`. A snapshot is generated only when the built source SHA equals the workflow SHA, which is the SHA the pinned action records. Rebuilding an older pinned environment skips a duplicate, incorrectly attributed snapshot; that source received its snapshot when it was originally built and released.

## S3 and CloudFront deployments

```yaml
schema_version: 2
application: example-spa
profile: spa-ecs

frontend:
  invalidation_paths: [/index.html, /assets/*]
```

The frontend profile uses the contract variables `RELEASE_FRONTEND_ARTIFACT_BUCKET`,
`RELEASE_FRONTEND_BUCKET`, and `RELEASE_FRONTEND_DISTRIBUTION_ID`. A static-site
may declare any number of targets and invalidations under `site.targets`. Every
S3 artifact is checked for at least one object before the first target sync
starts. An empty prefix is treated as missing, even when `aws s3 ls` exits
successfully.

## ECS and SSM deployments

```yaml
schema_version: 2
application: example-service
profile: ecs-service

backend:
  dockerfile: backend/Dockerfile
  services: [web, worker]
```

The default backend service is named `backend`. Set `backend.services` when one
image serves multiple ECS services. The generated contract names are
`RELEASE_ECS_<SERVICE>_CLUSTER`, `RELEASE_ECS_<SERVICE>_SERVICE`, and
`RELEASE_ECS_<SERVICE>_CONTAINER`; the default service uses the unsuffixed
`RELEASE_ECS_CLUSTER`, `RELEASE_ECS_SERVICE`, and `RELEASE_ECS_CONTAINER` keys.

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
- explicitly mapped named secrets supported by the reusable workflow

Adding a new arbitrary secret requires mapping a `HOOK_SECRET_*` name in the caller environment. Secrets are not packed into a JSON object.

AWS access-key, web-identity, GitHub-token, and Actions runtime credential variables are removed before each configured hook starts. Hooks must use the documented hook secrets and variables; they cannot assume the deploy job's AWS session or GitHub token.

## Repository dispatch

```yaml
events:
  repository_dispatch:
    gc-articles-update: [dev]
```

Unknown repository dispatch event types default to dev. Pull requests only plan and comment; they never build or deploy. Pushes build configured artifacts and reconcile every enabled environment. Manual runs default to dev and rebuild only when `rebuild` is selected.