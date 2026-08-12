# GitHub Actions experiments

Live experiments were run in [`cds-snc/nathaniel-actions-experiments`](https://github.com/cds-snc/nathaniel-actions-experiments) because several required behaviors are subtle and newer than the current local linter.

## Experiment 1: reusable workflow, environment matrix, and `$/` action

Commit: `8843d67adbe99f77ac739242406c0d20ba4add65`

Run: [31628317707](https://github.com/cds-snc/nathaniel-actions-experiments/actions/runs/31628317707)

The caller invoked a reusable workflow with `secrets: inherit`. The called workflow created a two-value matrix, selected `probe-alpha` and `probe-beta` as GitHub environments, checked out the caller, and ran a composite action through `$/`.

Assertions proved:

- each matrix job read its distinct environment variable
- checkout populated the caller workspace
- `$/` loaded the action from the workflow repository/ref rather than the workspace path
- `queue: max` was accepted by GitHub's workflow parser

## Experiment 2: nested reusable workflow

Commit: `1e25c39dd09961c5d3eef06fc875867656c5521e`

Run: [31629529135](https://github.com/cds-snc/nathaniel-actions-experiments/actions/runs/31629529135)

The topology was extended to caller -> reusable orchestrator -> reusable environment workflow -> `$/` composite action.

Both environment jobs passed. This validates the exact nesting pattern used by `release.yml`, `build.yml`, and `deploy-environment.yml`, including secret inheritance at each direct call.

## Linter compatibility

`actionlint` 1.7.12 does not yet recognize:

- `$/path` workflow/action references
- `concurrency.queue: max`

GitHub accepted and executed both constructs. All other workflow lint checks pass. The repository also has unit tests that require external actions to use full commit SHAs and verify the deployment build barrier.

Until actionlint adds these syntax forms, CI relies on GitHub's own workflow parser plus the Python workflow contract tests. Do not replace `$/` with a duplicated hardcoded tag; that would allow the workflow and its Python implementation to drift to different versions.