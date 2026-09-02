# Integration / acceptance test suite

The integration and acceptance suite runs the release pipeline at one explicit commit. It builds and deploys real artifacts to the testing AWS account.

## Test packages

Each acceptance test lives under `acceptance/tests/<id>/`. Its `test.yml` manifest, release configuration, application fixture, Terraform root, hooks, and verifier are kept together. Shared AWS, GitHub, and retry helpers live in `acceptance/support/`; the central runner only discovers packages, validates them, builds the GitHub matrix, and coordinates execution.

## Run from a release PR

Comment exactly `!test` on the release-please pull request. The comment workflow accepts requests from users with `push`, `maintain`, or `admin` repository permission, then dispatches `acceptance-tests.yml` at the pull request head SHA. It publishes the `Integration / acceptance tests` status for that exact SHA; a later commit has no passing status and must be tested again.

The suite applies the shared Terraform role first, then runs every enabled test package concurrently. Each package has its own Terraform state key and reconciles its own infrastructure before calling the release workflow. It does not destroy resources or clean artifacts after the run, so ECR, ECS, S3, logs, and workflow state remain available for inspection. State is cleaned before each run.

The repository unit tests also exercise the normalized configuration shape of all five client examples, push planning for every configured environment, and multi-service ECS deployment behavior. The live suite covers standard ECS, combined React and ECS, a failing build that prevents deployment, and an ECS health-check failure. Failure fixtures direct alert posts to test-owned Lambda recorders, which their verifiers inspect instead of sending messages to real Slack channels.

## Test isolation

Every current test has a separate VPC, public subnets, ALB, ECS cluster, ECS service, ECR repository, task roles, log group, and SSM image pointer. The React test also has separate private S3 artifact and site buckets. All tests use the single `acceptance-tests` GitHub environment, while the workflow passes a unique `pipeline_id` to each nested release pipeline so test concurrency groups cannot block or overwrite one another.
