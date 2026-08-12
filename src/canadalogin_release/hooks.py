from __future__ import annotations

from .commands import CommandRunner
from .config import ConfigError, PipelineConfig
from .runtime import RuntimeContext

HOOK_NAMES = {"before-deploy", "health-check", "after-deploy", "on-failure"}


def execute_hook(
    config: PipelineConfig,
    hook_name: str,
    context: RuntimeContext,
    *,
    force_redeploy: bool = False,
    runner: CommandRunner | None = None,
) -> None:
    if hook_name not in HOOK_NAMES:
        raise ConfigError(f"Unknown hook {hook_name!r}")
    runner = runner or CommandRunner()
    commands = getattr(config.hooks, hook_name.replace("-", "_"))
    environment = {
        **context.variables,
        "RELEASE_APPLICATION": config.application,
        "RELEASE_ENVIRONMENT": context.environment,
        "RELEASE_DEPLOYMENT_SHA": context.sha,
        "RELEASE_FORCE_REDEPLOY": "true" if force_redeploy else "false",
    }
    for command in commands:
        runner.run(command, cwd=context.repository, environment=environment)
