# Configuration example

This is an example for how the release pipeline is configured in a repository. Two files must be copied to your project. 

`.github/workflows/release-pipeline.yml` is the entrypoint workflow that invokes the central system. It can be copied exactly into your repository.

`.github/release-pipeline-configuration.yml` is the configuration that defines how your application is deployed. It should be updated per the [configuration doc](../docs/configuration.md).