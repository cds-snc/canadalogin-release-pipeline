# Configuration example

This is an example for how the release pipeline is configured in a repository. Two files must be copied to your project. 

`.github/workflows/release-pipeline.yml` is the entrypoint workflow that invokes the central system. It can be copied exactly into your repository.

`.github/release-pipeline-configuration.yml` is the configuration that defines how your application is deployed. It should be updated per the [configuration doc](../docs/configuration.md).

Keep the `pull_request` trigger enabled: the shared pipeline supplies the CRITICAL dependency vulnerability check without another caller workflow. After upgrading to a pipeline release containing this gate, require the **PR dependency vulnerabilities / Critical dependency vulnerabilities** check in your branch ruleset to block vulnerable PRs from merging. Every environment deployment is gated automatically against its own resolved SHA; no per-environment configuration is needed. See [dependency vulnerability gates](../README.md#dependency-vulnerability-gates) for lockfile requirements and scan coverage.