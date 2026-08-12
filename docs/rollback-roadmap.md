# Atomic deployment and rollback roadmap

Atomic rollback is not implemented in version 1. The current S3 sync changes many objects in place, and an ECS rolling update can pass through mixed task versions. The architecture deliberately creates the boundaries needed to add stronger guarantees.

## Improvements already present

- all required artifacts finish before deployment starts
- one job owns all components for one environment
- environments deploy sequentially
- workflow and environment concurrency prevent overlapping mutation
- S3 artifacts are preflighted before S3 mutation
- desired ECR images and all ECS service/task state are read before built-in S3 or ECS mutation
- health hooks run before success is announced
- SSM is updated only after ECS stability
- failure hooks and alerts know the exact environment

These prevent several inconsistent states, but they do not undo a mutation that succeeds before a later mutation fails.

## Target state model

Store two explicit states per environment:

- **DESIRED**: the version in `.deployed_versions`, or the main SHA for dev
- **CURRENT**: the last version whose complete application passed health checks

CURRENT should live in DynamoDB or SSM, separate from the per-service image parameters. A deployment transaction should record:

```json
{
  "deployment_id": "<repository>:<run-id>:<attempt>",
  "environment": "staging",
  "status": "preparing",
  "original_version": "1.2.2",
  "desired_version": "1.2.3",
  "components": {},
  "lease_expires_at": "..."
}
```

Use a conditional write to acquire the environment lock. GitHub concurrency remains a useful first layer, but an AWS lock protects against manual operations and independent workflows.

## Transaction phases

### 1. Discover

- read CURRENT and DESIRED
- snapshot CloudFront origin configuration, S3 release pointer, ECS task definitions, service desired counts, and SSM values
- create an idempotent transaction record

### 2. Prepare

- ensure every immutable artifact exists
- publish frontend files under a versioned prefix without changing live traffic
- create new ECS task definitions
- start blue/green replacement task sets without shifting production traffic
- run component-level health checks against prepared resources

No customer-visible state changes in this phase.

### 3. Commit

- switch the frontend origin/path pointer to the desired immutable prefix
- shift backend traffic to the prepared task set
- keep the cutover operations close together and record each result
- run application-level health checks through public endpoints

### 4. Finalize

- write CURRENT only after every component and health check succeeds
- update per-service SSM image parameters
- retire old resources after a safety window
- mark the transaction committed and notify Slack

### 5. Roll back

On prepare, commit, or health failure:

- restore the original CloudFront/S3 pointer
- shift backend traffic to the original task set
- restore original SSM values
- verify public health against CURRENT
- mark the transaction rolled back or rollback-failed
- send a high-priority Slack alert with both desired and restored versions

Rollback must be idempotent so a rerun can finish recovery after runner or API failure.

## Infrastructure changes required

### Frontend

Stop syncing release contents into one live bucket prefix. Store every build under an immutable version/SHA prefix and switch a CloudFront origin path, key-value pointer, or equivalent single control-plane value.

### Backend

Adopt ECS blue/green deployments, likely through CodeDeploy or explicit task sets behind an ALB. The pipeline must be able to health-check the replacement before traffic shift and retain the prior task set for rollback.

### State and locking

Use DynamoDB when conditional locks, transaction history, and component state need one consistent record. SSM can hold simple CURRENT pointers but is less suitable as the transaction coordinator.

## Compatibility with version 1

The caller workflow and TOML can remain stable. The Python deploy engine can add `discover`, `prepare`, `commit`, `verify`, and `rollback` phases behind the existing S3/ECS declarations. Existing health hooks become the application verification phase. New configuration should be additive, for example blue/green target groups or frontend pointer details.

Automatic rollback should be enabled per environment only after repeated prepare/commit/rollback drills and alarms for rollback failure are in place.