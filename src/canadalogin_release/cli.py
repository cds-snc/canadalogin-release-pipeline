from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from .build import execute_build
from .config import ConfigError, PipelineConfig
from .deploy import (
    deploy_ecs,
    deploy_s3,
    preflight_ecs,
    preflight_s3,
    target_context,
)
from .github import promotions_from_json, sync_deployment_comment
from .hooks import execute_hook
from .notifications import (
    notify,
    notify_pipeline_failure,
    pipeline_failure_webhook_values,
)
from .planner import create_plan
from .runtime import RuntimeContext, variables_from_environment
from .validation import validate_repository
from .versions import release_tag_for_sha


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="canadalogin-release")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate configuration")
    validate_parser.add_argument("--config", required=True)

    plan_parser = subparsers.add_parser("plan", help="Plan workflow jobs")
    plan_parser.add_argument("--config", required=True)
    plan_parser.add_argument("--event-name", required=True)
    plan_parser.add_argument("--sha", required=True)
    plan_parser.add_argument("--before-sha", default="")
    plan_parser.add_argument("--repository", default=".")
    plan_parser.add_argument("--environment", default="")
    plan_parser.add_argument("--repository-dispatch-event", default="")
    plan_parser.add_argument(
        "--force-redeploy",
        action="store_true",
    )
    plan_parser.add_argument("--rebuild", action="store_true")
    plan_parser.add_argument("--github-output", action="store_true")

    build_parser = subparsers.add_parser("build", help="Build and publish an artifact")
    build_parser.add_argument("--config", required=True)
    build_parser.add_argument("--name", required=True)
    build_parser.add_argument("--environment", required=True)
    build_parser.add_argument("--sha", required=True)
    build_parser.add_argument("--source-sha", default="")
    build_parser.add_argument("--repository", default=".")
    build_parser.add_argument("--github-ref", default="")
    build_parser.add_argument("--github-output", action="store_true")

    for command in ("preflight-s3", "preflight-ecs", "deploy-s3", "deploy-ecs"):
        deploy_parser = subparsers.add_parser(
            command, help=f"Run {command} deployments"
        )
        deploy_parser.add_argument("--config", required=True)
        deploy_parser.add_argument("--environment", required=True)
        deploy_parser.add_argument("--sha", required=True)
        deploy_parser.add_argument("--repository", default=".")
        deploy_parser.add_argument(
            "--force-redeploy",
            action="store_true",
        )
        deploy_parser.add_argument("--github-output", action="store_true")

    hook_parser = subparsers.add_parser("hook", help="Run a repository lifecycle hook")
    hook_parser.add_argument("hook_name")
    hook_parser.add_argument("--config", required=True)
    hook_parser.add_argument("--environment", required=True)
    hook_parser.add_argument("--sha", required=True)
    hook_parser.add_argument("--repository", default=".")
    hook_parser.add_argument(
        "--force-redeploy",
        action="store_true",
    )

    notify_parser = subparsers.add_parser("notify", help="Send a Slack notification")
    notify_parser.add_argument("--status", required=True)
    notify_parser.add_argument("--config", required=True)
    notify_parser.add_argument("--environment", required=True)
    notify_parser.add_argument("--sha", required=True)
    notify_parser.add_argument("--repository", default=".")
    notify_parser.add_argument("--workflow-url", required=True)
    notify_parser.add_argument("--detail", default="")

    comment_parser = subparsers.add_parser(
        "pr-comment", help="Create or update the deployment impact comment"
    )
    comment_parser.add_argument("--config", required=True)
    comment_parser.add_argument("--repository-name", required=True)
    comment_parser.add_argument("--pull-request", required=True, type=int)
    comment_parser.add_argument("--api-url", default="https://api.github.com")

    pipeline_failure_parser = subparsers.add_parser(
        "pipeline-failure", help="Send a configuration-independent failure alert"
    )
    pipeline_failure_parser.add_argument("--application", required=True)
    pipeline_failure_parser.add_argument("--workflow-url", required=True)
    pipeline_failure_parser.add_argument(
        "--detail", default="planning or release-please"
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    parser = build_parser()
    options = parser.parse_args(arguments)
    try:
        if options.command == "pipeline-failure":
            result = notify_pipeline_failure(
                options.application,
                options.workflow_url,
                pipeline_failure_webhook_values(os.environ),
                detail=options.detail,
            )
            print(
                json.dumps({"delivered": result.delivered, "skipped": result.skipped})
            )
            return 0
        config = PipelineConfig.load(options.config)
        if options.command == "validate":
            print(f"Configuration is valid for {config.application}.")
            return 0
        if options.command == "plan":
            force_redeploy = options.force_redeploy or _environment_bool(
                "RELEASE_FORCE_REDEPLOY"
            )
            rebuild = options.rebuild or _environment_bool("RELEASE_REBUILD")
            validation = validate_repository(config, options.repository)
            for warning in validation.warnings:
                print(f"::warning::{warning}")
            plan = create_plan(
                config,
                event_name=options.event_name,
                sha=options.sha,
                repository=options.repository,
                before_sha=options.before_sha,
                manual_environment=options.environment,
                force_redeploy=force_redeploy,
                rebuild=rebuild,
                repository_dispatch_event=options.repository_dispatch_event,
            )
            outputs = plan.github_outputs()
            if options.github_output:
                _write_github_outputs(outputs)
            print(json.dumps(outputs, indent=2, sort_keys=True))
            return 0
        if options.command == "build":
            release_tag = release_tag_for_sha(config, options.sha, options.repository)
            context = RuntimeContext.create(
                repository=options.repository,
                environment=options.environment,
                sha=options.sha,
                release_tag=release_tag,
                github_ref=options.github_ref,
                variables=variables_from_environment(),
            )
            result = execute_build(
                config,
                build_name=options.name,
                context=context,
                source_sha=options.source_sha or None,
            )
            outputs = result.github_outputs()
            if options.github_output:
                _write_github_outputs(outputs)
            print(json.dumps(outputs, indent=2, sort_keys=True))
            return 0
        if options.command in {
            "preflight-s3",
            "preflight-ecs",
            "deploy-s3",
            "deploy-ecs",
            "hook",
        }:
            force_redeploy = options.force_redeploy or _environment_bool(
                "RELEASE_FORCE_REDEPLOY"
            )
            context = RuntimeContext.create(
                repository=options.repository,
                environment=options.environment,
                sha=options.sha,
                release_tag=None,
                github_ref="",
                variables=variables_from_environment(),
            )
            if options.command == "hook":
                context = target_context(config, context)
                execute_hook(
                    config,
                    options.hook_name,
                    context,
                    force_redeploy=force_redeploy,
                )
                return 0
            if options.command == "preflight-s3":
                result = preflight_s3(config, context)
            elif options.command == "preflight-ecs":
                result = preflight_ecs(config, context)
            elif options.command == "deploy-s3":
                result = deploy_s3(config, context)
            else:
                result = deploy_ecs(config, context, force_redeploy=force_redeploy)
            outputs = result.github_outputs()
            if options.github_output:
                _write_github_outputs(outputs)
            print(json.dumps(outputs, indent=2, sort_keys=True))
            return 0
        if options.command == "notify":
            context = RuntimeContext.create(
                repository=options.repository,
                environment=options.environment,
                sha=options.sha,
                release_tag=None,
                github_ref="",
                variables=variables_from_environment(),
            )
            result = notify(
                config,
                options.status,
                context,
                workflow_url=options.workflow_url,
                detail=options.detail,
            )
            print(
                json.dumps({"delivered": result.delivered, "skipped": result.skipped})
            )
            return 0
        if options.command == "pr-comment":
            promotions = promotions_from_json(
                os.environ.get("RELEASE_PROMOTIONS", "[]")
            )
            result = sync_deployment_comment(
                repository=options.repository_name,
                pull_request=options.pull_request,
                promotions=promotions,
                token=os.environ.get("GITHUB_TOKEN", ""),
                api_url=options.api_url,
            )
            print(
                json.dumps({"action": result.action, "comment_id": result.comment_id})
            )
            return 0
    except ConfigError as error:
        parser.exit(2, f"error: {error}\n")
    return 1


def _write_github_outputs(outputs: dict[str, str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        raise ConfigError("GITHUB_OUTPUT is not set")
    with Path(output_path).open("a") as output_file:
        output_file.writelines(f"{name}={value}\n" for name, value in outputs.items())


def _environment_bool(name: str) -> bool:
    value = os.environ.get(name, "false").strip().lower()
    if value not in {"true", "false"}:
        raise ConfigError(f"{name} must be 'true' or 'false'")
    return value == "true"


if __name__ == "__main__":
    sys.exit(main())
