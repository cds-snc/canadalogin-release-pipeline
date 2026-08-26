# Integration / acceptance test suite

The integration and acceptance suite runs the release pipeline at one explicit
commit. It is intentionally opt-in because it builds and deploys real artifacts
to the scratch AWS account.

The checked-in fixtures use account `014097726303` in `ca-central-1`.

## Test packages

Each acceptance test lives under `acceptance/tests/<id>/`. Its `test.yml`
manifest, release configuration, application fixture, Terraform root, hooks,
and verifier are kept together. Shared AWS, GitHub, and retry helpers live in
`acceptance/support/`; the central runner only discovers packages, validates
them, builds the GitHub matrix, and coordinates execution.

List and validate the packages locally:

```sh
PYTHONPATH=src python3 acceptance/runner.py list
PYTHONPATH=src python3 acceptance/runner.py validate --all
PYTHONPATH=src python3 acceptance/runner.py local --all
```

Add `--build` to the local command to run the fixture command and Docker builds
without pushing anything to AWS. The local preflight does not require AWS or
GitHub credentials.

## Run from a release PR

Comment exactly `!test` on an open pull request in
`cds-snc/canadalogin-release-system`. The comment workflow accepts requests from
users with `push`, `maintain`, or `admin` repository permission, then dispatches
`release-pipeline-tests.yml` at the pull request head SHA. It publishes the
`Integration / acceptance tests` status for that exact SHA; a later commit has
no passing status and must be tested again.

Before the first run, create the GitHub environment `acceptance-tests`. It does
not need secrets, but its name is part of the OIDC trust policies for the AWS
roles.

The suite applies the shared Terraform role first, then runs every enabled
test package concurrently. Each package has its own Terraform state key and
reconciles its own infrastructure before calling the release workflow. It does
not destroy resources or clean artifacts after the run, so ECR, ECS, S3, logs,
and workflow state remain available for inspection.

The repository unit tests also exercise the normalized configuration shape of
all five client examples, push planning for every configured environment, and
multi-service ECS deployment behavior. The live suite covers the standard ECS,
combined React and ECS, and expected health-hook failure paths.

## Test isolation

Every current test has a separate VPC, public subnets, ALB, ECS cluster, ECS
service, ECR repository, task roles, log group, and SSM image pointer. The React
test also has separate private S3 artifact and site buckets. All tests use the
single `acceptance-tests` GitHub environment, while the workflow passes a
unique `pipeline_id` to each nested release pipeline so test concurrency groups
cannot block or overwrite one another.
