from __future__ import annotations

import json
import re
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
            "expected_release_result": self.release["expected_release_result"],
            "notification_capture_function": self.resources.get(
                "notification_capture_function", ""
            ),
            "rebuild": self.release["rebuild"],
            "release_pipeline_vars": self.release_pipeline_vars(
                release_account_id, release_region
            ),
        }

    def release_pipeline_vars(self, account_id: str, region: str) -> str:
        values = {
            "RELEASE_ECS_ROLE": self.verification_role,
            "RELEASE_ECR_REPOSITORY": (
                f"{account_id}.dkr.ecr.{region}.amazonaws.com/"
                f"{_resolve_resource(self.resources['ecr_repository'], account_id, region)}"
            ),
            "RELEASE_ECS_CLUSTER": _resolve_resource(
                self.resources["cluster"], account_id, region
            ),
            "RELEASE_ECS_SERVICE": _resolve_resource(
                self.resources["service"], account_id, region
            ),
            "RELEASE_ECS_CONTAINER": "app",
        }
        if "artifact_bucket" in self.resources:
            values.update(
                {
                    "RELEASE_S3_ROLE": self.verification_role,
                    "RELEASE_FRONTEND_ARTIFACT_BUCKET": _resolve_resource(
                        self.resources["artifact_bucket"], account_id, region
                    ),
                    "RELEASE_FRONTEND_BUCKET": _resolve_resource(
                        self.resources["site_bucket"], account_id, region
                    ),
                }
            )
        return json.dumps(values, separators=(",", ":"), sort_keys=True)


def relative_path(value: Path) -> str:
    return value.resolve().relative_to(ROOT.resolve()).as_posix()


def _resolve_resource(value: str, account_id: str, region: str) -> str:
    try:
        return value.format(aws_account_id=account_id, aws_region=region)
    except (KeyError, ValueError) as error:
        raise CatalogError(f"Invalid resource placeholder {value!r}: {error}") from error


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
            "expected_release_result",
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
    expected_release_result = release.get("expected_release_result", "success")
    if expected_release_result not in {"success", "failure"}:
        raise CatalogError(
            f"{manifest_path}.release.expected_release_result must be success or failure"
        )
    release["expected_release_result"] = expected_release_result
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
            "notification_capture_function",
            "notification_capture_table",
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
    if expected_release_result == "failure":
        required_failure_resources = {
            "notification_capture_function",
            "notification_capture_table",
        }
        missing_failure_resources = sorted(required_failure_resources - set(resources))
        if missing_failure_resources:
            raise CatalogError(
                f"{manifest_path}.verification.resources is missing for an expected failure: "
                f"{', '.join(missing_failure_resources)}"
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
