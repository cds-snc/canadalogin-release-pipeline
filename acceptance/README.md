# Release pipeline acceptance suite

The acceptance suite runs the release pipeline at one explicit commit. It is
intentionally opt-in because it builds and deploys real artifacts to the
scratch AWS account.

The checked-in fixtures default to account `014097726303` in `ca-central-1`.
The dispatch account and region inputs are passed through to Terraform, AWS
credentials, artifact URIs, and live verification, so a dedicated account and
region can be selected without changing the fixtures.

## Run from a release PR

Comment exactly `!test` on an open pull request in
`cds-snc/canadalogin-release-system`. The comment workflow accepts requests from
users with `push`, `maintain`, or `admin` repository permission, then dispatches
`release-pipeline-tests.yml` at the pull request head SHA.

Before the first run, create the GitHub environments
`acceptance-terraform`, `acceptance-standard`, `acceptance-react`, and
`acceptance-failure`. They do not need secrets, but their names are part of the
OIDC trust policies for the AWS roles.

The suite applies Terraform first, clears scenario state before the run, and
runs the standard ECS, React plus ECS, and expected health-hook failure
scenarios concurrently. It does not destroy resources or clean artifacts after
the run, so ECR, ECS, S3, logs, and workflow state remain available for
inspection.

## One-time scratch bootstrap

The first apply must be performed by an administrator with the scratch
credentials. The apply creates the Terraform OIDC role, three scenario roles,
and isolated scenario infrastructure. It also creates the S3 state bucket and
DynamoDB lock table through the workflow bootstrap script.

```sh
source ../../agent-artifacts/.aws-keys
export AWS_REGION=ca-central-1
export AWS_DEFAULT_REGION="$AWS_REGION" AWS_PAGER=cat AWS_CLI_AUTO_PROMPT=off
aws sts get-caller-identity --query Account --output text

export TF_STATE_BUCKET=cl-acceptance-tfstate-014097726303
export TF_STATE_LOCK_TABLE=cl-acceptance-tfstate-lock-014097726303
./acceptance/scripts/bootstrap-terraform-state.sh
terraform -chdir=acceptance/terraform init -input=false -reconfigure \
  -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config="key=release-pipeline-acceptance/terraform.tfstate" \
  -backend-config="region=$AWS_DEFAULT_REGION" \
  -backend-config="dynamodb_table=$TF_STATE_LOCK_TABLE"
terraform -chdir=acceptance/terraform apply -input=false -auto-approve
```

After this bootstrap, the workflow can assume
`cl-acceptance-terraform` through GitHub OIDC. The Terraform role is deliberately
broad within the scratch account so it can reconcile this disposable stack; use
a dedicated account and narrower permissions before adopting this pattern for
shared or production infrastructure.

## Scenario isolation

Every scenario has a separate VPC, public subnets, ALB, ECS cluster, ECS
service, ECR repository, task roles, log group, and SSM image pointer. The React
scenario also has separate private S3 artifact and site buckets. The workflow
passes a unique `pipeline_id` to each nested release pipeline, so scenario
concurrency groups cannot block or overwrite one another.
