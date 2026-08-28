# Development

This document covers local development and the checks used by the release system.

## Setup

The release system requires Python 3.11 or later. Create a virtual environment and
install the package from the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --require-hashes --no-deps -r requirements.lock
python -m pip install --no-build-isolation --no-deps -e .
```

`requirements.lock` pins the runtime and build dependencies used by CI and
release workflows. Regenerate it after modifying `requirements.in`:

```sh
uv pip compile --generate-hashes --output-file requirements.lock requirements.in
```

## Unit tests

```sh
python -m compileall -q src
python -m unittest discover -s tests -v
```

Unit tests run on every pull request.

## Acceptance tests

The integration and acceptance suite builds and deploys real applications to an
AWS account, then verifies that the release pipeline behaves as expected.

List and validate the test packages locally:

```sh
PYTHONPATH=src python3 acceptance/runner.py list
PYTHONPATH=src python3 acceptance/runner.py validate --all
```

Before merging a release-please pull request, the live acceptance suite must pass.
CI will prevent the pull request from merging without the required status.

To request the suite, comment exactly `!test` on the open release-please pull
request targeting `main`.