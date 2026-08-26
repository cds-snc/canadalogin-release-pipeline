from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from canadalogin_release.config import ConfigError, PipelineConfig

TESTS_ROOT = ROOT / "acceptance" / "tests"
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
GITHUB_ENVIRONMENT = "acceptance-tests"


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class AcceptanceTest:
    test_id: str
    name: str
    package: Path
    config_path: Path
    terraform_directory: Path
    verify_script: Path
    release: dict[str, object]
    verification_role: str
    resources: dict[str, str]
    enabled: bool

    def matrix_item(self, account_id: str, region: str) -> dict[str, object]:
        release_account_id = str(self.release.get("aws_account_id") or account_id)
        release_region = str(self.release.get("aws_region") or region)
        return {
            "id": self.test_id,
            "name": self.name,
            "config_path": relative_path(self.config_path),
            "terraform_directory": relative_path(self.terraform_directory),
            "verify_script": relative_path(self.verify_script),
            "environment": self.release["environment"],
            "github_environment": self.release["github_environment"],
            "aws_role": self.verification_role,
            "aws_account_id": release_account_id,
            "aws_region": release_region,
            "pipeline_id_prefix": self.release["pipeline_id_prefix"],
            "create_deployment": self.release["create_deployment"],
            "expect_health_check_failure": self.release["expect_health_check_failure"],
            "rebuild": self.release["rebuild"],
        }


def relative_path(value: Path) -> str:
    return value.resolve().relative_to(ROOT.resolve()).as_posix()


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CatalogError(f"{field} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise CatalogError(f"{field} keys must be strings")
    return dict(value)


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{field} must be a non-empty string")
    return value


def _boolean(value: object, field: str, default: bool | None = None) -> bool:
    if value is None and default is not None:
        return default
    if not isinstance(value, bool):
        raise CatalogError(f"{field} must be a boolean")
    return value


def _reject_unknown(mapping: dict[str, object], allowed: set[str], field: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise CatalogError(f"{field} has unknown fields: {', '.join(unknown)}")


def _owned_path(value: object, package: Path, field: str) -> Path:
    raw_path = _string(value, field)
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise CatalogError(f"{field} must be a relative path without '..'")

    package_root = package.resolve()
    candidates = [(ROOT / relative).resolve(), (package / relative).resolve()]
    for candidate in candidates:
        try:
            candidate.relative_to(package_root)
        except ValueError:
            continue
        if candidate.exists():
            return candidate

    raise CatalogError(f"{field} does not exist inside {package_root}: {raw_path}")


def _load_manifest(manifest_path: Path) -> AcceptanceTest:
    package = manifest_path.parent
    try:
        document = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise CatalogError(f"Unable to read {manifest_path}: {error}") from error

    manifest = _mapping(document, str(manifest_path))
    _reject_unknown(
        manifest,
        {
            "schema_version",
            "id",
            "name",
            "enabled",
            "release",
            "infrastructure",
            "verification",
        },
        str(manifest_path),
    )
    if manifest.get("schema_version") != 1:
        raise CatalogError(f"{manifest_path} must use schema_version 1")

    test_id = _string(manifest.get("id"), f"{manifest_path}.id")
    if not ID_PATTERN.fullmatch(test_id):
        raise CatalogError(f"{manifest_path}.id is not a slug: {test_id}")
    if test_id != package.name:
        raise CatalogError(f"{manifest_path}.id must match its directory name")

    release = _mapping(manifest.get("release"), f"{manifest_path}.release")
    _reject_unknown(
        release,
        {
            "config_path",
            "environment",
            "github_environment",
            "pipeline_id_prefix",
            "create_deployment",
            "expect_health_check_failure",
            "rebuild",
            "aws_account_id",
            "aws_region",
        },
        f"{manifest_path}.release",
    )
    config_path = _owned_path(
        release.get("config_path"), package, f"{manifest_path}.release.config_path"
    )
    required_release = {
        "environment",
        "github_environment",
        "pipeline_id_prefix",
    }
    for field in required_release:
        _string(release.get(field), f"{manifest_path}.release.{field}")
    if release["github_environment"] != GITHUB_ENVIRONMENT:
        raise CatalogError(
            f"{manifest_path}.release.github_environment must be {GITHUB_ENVIRONMENT!r}"
        )
    pipeline_id_prefix = str(release["pipeline_id_prefix"])
    if not ID_PATTERN.fullmatch(pipeline_id_prefix):
        raise CatalogError(
            f"{manifest_path}.release.pipeline_id_prefix is not a slug: {pipeline_id_prefix}"
        )
    release["create_deployment"] = _boolean(
        release.get("create_deployment"),
        f"{manifest_path}.release.create_deployment",
        default=False,
    )
    release["expect_health_check_failure"] = _boolean(
        release.get("expect_health_check_failure"),
        f"{manifest_path}.release.expect_health_check_failure",
        default=False,
    )
    release["rebuild"] = _boolean(
        release.get("rebuild"), f"{manifest_path}.release.rebuild", default=True
    )
    for field in ("aws_account_id", "aws_region"):
        if field in release:
            _string(release[field], f"{manifest_path}.release.{field}")

    infrastructure = _mapping(
        manifest.get("infrastructure"), f"{manifest_path}.infrastructure"
    )
    _reject_unknown(
        infrastructure, {"terraform_directory"}, f"{manifest_path}.infrastructure"
    )
    terraform_directory = _owned_path(
        infrastructure.get("terraform_directory"),
        package,
        f"{manifest_path}.infrastructure.terraform_directory",
    )
    if not (terraform_directory / "main.tf").is_file():
        raise CatalogError(f"{terraform_directory} must contain main.tf")

    verification = _mapping(
        manifest.get("verification"), f"{manifest_path}.verification"
    )
    _reject_unknown(
        verification,
        {"script", "aws_role", "resources"},
        f"{manifest_path}.verification",
    )
    verify_script = _owned_path(
        verification.get("script"), package, f"{manifest_path}.verification.script"
    )
    if verify_script.suffix != ".py":
        raise CatalogError(f"{verify_script} must be a Python verifier")
    verification_role = _string(
        verification.get("aws_role"), f"{manifest_path}.verification.aws_role"
    )
    resources_raw = _mapping(
        verification.get("resources"), f"{manifest_path}.verification.resources"
    )
    _reject_unknown(
        resources_raw,
        {
            "app_name",
            "ecr_repository",
            "cluster",
            "service",
            "ssm_parameter",
            "site_bucket",
            "artifact_bucket",
        },
        f"{manifest_path}.verification.resources",
    )
    required_resources = {
        "app_name",
        "ecr_repository",
        "cluster",
        "service",
        "ssm_parameter",
    }
    resources: dict[str, str] = {}
    for field, value in resources_raw.items():
        resources[field] = _string(
            value, f"{manifest_path}.verification.resources.{field}"
        )
    missing_resources = sorted(required_resources - set(resources))
    if missing_resources:
        raise CatalogError(
            f"{manifest_path}.verification.resources is missing: {', '.join(missing_resources)}"
        )

    return AcceptanceTest(
        test_id=test_id,
        name=_string(manifest.get("name"), f"{manifest_path}.name"),
        package=package,
        config_path=config_path,
        terraform_directory=terraform_directory,
        verify_script=verify_script,
        release=release,
        verification_role=verification_role,
        resources=resources,
        enabled=_boolean(manifest.get("enabled"), f"{manifest_path}.enabled", True),
    )


def discover() -> list[AcceptanceTest]:
    if not TESTS_ROOT.is_dir():
        raise CatalogError(f"Acceptance test directory is missing: {TESTS_ROOT}")
    manifests = sorted(TESTS_ROOT.glob("*/test.yml"))
    if not manifests:
        raise CatalogError(f"No acceptance test manifests found under {TESTS_ROOT}")

    tests = [_load_manifest(manifest) for manifest in manifests]
    ids = [test.test_id for test in tests]
    if len(ids) != len(set(ids)):
        raise CatalogError("Acceptance test IDs must be unique")
    return tests


def validate_config(test: AcceptanceTest) -> None:
    try:
        PipelineConfig.load(str(test.config_path))
    except ConfigError as error:
        raise CatalogError(
            f"{test.test_id} has an invalid release configuration: {error}"
        ) from error


def validate_catalog(tests: list[AcceptanceTest]) -> None:
    for test in tests:
        validate_config(test)
    if not any(test.enabled for test in tests):
        raise CatalogError("At least one acceptance test must be enabled")


def select_tests(
    tests: list[AcceptanceTest], test_id: str | None
) -> list[AcceptanceTest]:
    if test_id is None:
        selected = [test for test in tests if test.enabled]
    else:
        selected = [test for test in tests if test.test_id == test_id]
        if not selected:
            raise CatalogError(f"Unknown acceptance test: {test_id}")
        if not selected[0].enabled:
            raise CatalogError(f"Acceptance test is disabled: {test_id}")
    if not selected:
        raise CatalogError("No enabled acceptance tests selected")
    return selected


def _format_value(value: object, release_sha: str) -> str:
    return (
        str(value)
        .replace("{sha}", release_sha)
        .replace("{release_version}", release_sha)
    )


def _run_local(command: list[str], cwd: Path, environment: dict[str, str]) -> None:
    print(f"$ (cd {cwd.relative_to(ROOT)} && {' '.join(command)})")
    subprocess.run(command, cwd=cwd, env=environment, check=True)


def run_local_builds(test: AcceptanceTest, release_sha: str) -> None:
    document = yaml.safe_load(test.config_path.read_text(encoding="utf-8"))
    builds = document.get("builds", []) if isinstance(document, dict) else []
    if not isinstance(builds, list):
        raise CatalogError(
            f"{test.test_id} release configuration builds must be a list"
        )

    environment = os.environ.copy()
    environment.update(
        {
            "RELEASE_SHA": release_sha,
            "VITE_RELEASE_SHA": release_sha,
        }
    )
    for build in builds:
        build_mapping = _mapping(build, f"{test.test_id}.build")
        name = _string(build_mapping.get("name"), f"{test.test_id}.build.name")
        kind = _string(build_mapping.get("kind"), f"{test.test_id}.build.kind")
        if kind == "command":
            command_mapping = _mapping(
                build_mapping.get("command"), f"{test.test_id}.{name}.command"
            )
            working_directory = ROOT / Path(
                _string(
                    command_mapping.get("working_directory", "."),
                    f"{test.test_id}.{name}.command.working_directory",
                )
            )
            steps = command_mapping.get("steps", [])
            if not isinstance(steps, list):
                raise CatalogError(
                    f"{test.test_id}.{name}.command.steps must be a list"
                )
            local_environment = environment.copy()
            command_environment = command_mapping.get("environment", {})
            if isinstance(command_environment, dict):
                local_environment.update(
                    {
                        str(key): _format_value(value, release_sha)
                        for key, value in command_environment.items()
                    }
                )
            for step in steps:
                if (
                    not isinstance(step, list)
                    or not step
                    or any(
                        not isinstance(part, (str, int, float, bool)) for part in step
                    )
                ):
                    raise CatalogError(
                        f"{test.test_id}.{name}.command.steps entries must be non-empty argument lists"
                    )
                _run_local(
                    [_format_value(part, release_sha) for part in step],
                    working_directory,
                    local_environment,
                )
        elif kind == "docker":
            docker = _mapping(
                build_mapping.get("docker"), f"{test.test_id}.{name}.docker"
            )
            context = ROOT / Path(
                _string(docker.get("context"), f"{test.test_id}.{name}.docker.context")
            )
            dockerfile = ROOT / Path(
                _string(
                    docker.get("dockerfile"), f"{test.test_id}.{name}.docker.dockerfile"
                )
            )
            image = f"acceptance-local-{test.test_id}-{name}:local"
            command = ["docker", "build", "--file", str(dockerfile), "--tag", image]
            build_args = docker.get("build_args", {})
            if isinstance(build_args, dict):
                for key, value in build_args.items():
                    command.extend(
                        ["--build-arg", f"{key}={_format_value(value, release_sha)}"]
                    )
            command.append(str(context))
            _run_local(command, ROOT, environment)
        else:
            print(
                f"Skipping unsupported local build kind {kind!r} for {test.test_id}/{name}."
            )


def run_verifier(
    test: AcceptanceTest,
    release_sha: str,
    pipeline_result: str,
    expected_result: str,
) -> int:
    environment = os.environ.copy()
    python_path = os.pathsep.join(
        value
        for value in (str(ROOT), str(SRC), environment.get("PYTHONPATH", ""))
        if value
    )
    environment.update(
        {
            "ACCEPTANCE_TEST_ID": test.test_id,
            "ACCEPTANCE_RESOURCES": json.dumps(test.resources, sort_keys=True),
            "RELEASE_SHA": release_sha,
            "PIPELINE_RESULT": pipeline_result,
            "EXPECTED_RESULT": expected_result,
            "PYTHONPATH": python_path,
        }
    )
    result = subprocess.run(
        [sys.executable, str(test.verify_script)],
        cwd=ROOT,
        env=environment,
        check=False,
    )
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="acceptance-runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List discovered acceptance tests")

    validate_parser = subparsers.add_parser(
        "validate", help="Validate test manifests and release configs"
    )
    validate_parser.add_argument("--test", dest="test_id")
    validate_parser.add_argument("--all", action="store_true")

    matrix_parser = subparsers.add_parser(
        "matrix", help="Emit the enabled GitHub Actions matrix"
    )
    matrix_parser.add_argument("--github-output", action="store_true")
    matrix_parser.add_argument(
        "--aws-account-id", default=os.environ.get("AWS_ACCOUNT_ID", "014097726303")
    )
    matrix_parser.add_argument(
        "--aws-region", default=os.environ.get("AWS_REGION", "ca-central-1")
    )

    local_parser = subparsers.add_parser(
        "local", help="Run local acceptance preflight checks"
    )
    local_parser.add_argument("--test", dest="test_id")
    local_parser.add_argument("--all", action="store_true")
    local_parser.add_argument("--sha", default=os.environ.get("RELEASE_SHA", "local"))
    local_parser.add_argument("--build", action="store_true")

    verify_parser = subparsers.add_parser(
        "verify", help="Run one test-owned live verifier"
    )
    verify_parser.add_argument("--test", dest="test_id", required=True)
    verify_parser.add_argument("--sha", default=os.environ.get("RELEASE_SHA", ""))
    verify_parser.add_argument(
        "--pipeline-result", default=os.environ.get("PIPELINE_RESULT", "")
    )
    verify_parser.add_argument(
        "--expected-result", default=os.environ.get("EXPECTED_RESULT", "success")
    )
    return parser


def main(arguments: list[str] | None = None) -> int:
    parser = build_parser()
    options = parser.parse_args(arguments)
    try:
        tests = discover()
        if options.command == "list":
            for test in tests:
                state = "enabled" if test.enabled else "disabled"
                print(f"{test.test_id}\t{state}\t{test.name}")
            return 0

        if options.command == "validate":
            validate_catalog(tests)
            selected = select_tests(tests, options.test_id)
            for test in selected:
                print(f"Validated {test.test_id}: {test.config_path.relative_to(ROOT)}")
            return 0

        if options.command == "matrix":
            validate_catalog(tests)
            matrix = [
                test.matrix_item(options.aws_account_id, options.aws_region)
                for test in select_tests(tests, None)
            ]
            encoded = json.dumps(matrix, separators=(",", ":"), sort_keys=True)
            if options.github_output:
                output_path = os.environ.get("GITHUB_OUTPUT")
                if not output_path:
                    raise CatalogError("GITHUB_OUTPUT is required with --github-output")
                with open(output_path, "a", encoding="utf-8") as output:
                    output.write(f"matrix={encoded}\n")
            print(encoded)
            return 0

        if options.command == "local":
            validate_catalog(tests)
            for test in select_tests(tests, options.test_id):
                print(f"Running local preflight for {test.test_id}")
                if options.build:
                    run_local_builds(test, options.sha)
            print("Local acceptance preflight passed.")
            return 0

        if options.command == "verify":
            validate_catalog(tests)
            test = select_tests(tests, options.test_id)[0]
            return run_verifier(
                test,
                options.sha,
                options.pipeline_result,
                options.expected_result,
            )
    except (CatalogError, ConfigError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(2, f"error: {error}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
