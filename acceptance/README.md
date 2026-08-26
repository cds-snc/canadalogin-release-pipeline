# Integration / acceptance test suite

The integration and acceptance suite runs the release pipeline at one explicit
commit. It is intentionally opt-in because it builds and deploys real artifacts
to the scratch AWS account.

The checked-in fixtures use account `014097726303` in `ca-central-1`.

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

The suite applies Terraform first to reconcile the acceptance infrastructure
and clear per-run scenario state, then runs the standard ECS, React plus ECS,
and expected health-hook failure scenarios concurrently. It does not destroy
resources or clean artifacts after the run, so ECR, ECS, S3, logs, and workflow
state remain available for inspection.

The repository unit tests also exercise the normalized configuration shape of
all five client examples, push planning for every configured environment, and
multi-service ECS deployment behavior. The live suite covers the standard ECS,
combined React and ECS, and expected health-hook failure paths.

## Scenario isolation

Every scenario has a separate VPC, public subnets, ALB, ECS cluster, ECS
service, ECR repository, task roles, log group, and SSM image pointer. The React
scenario also has separate private S3 artifact and site buckets. All scenarios
use the single `acceptance-tests` GitHub environment, while the workflow passes
a unique `pipeline_id` to each nested release pipeline so scenario concurrency
groups cannot block or overwrite one another.
