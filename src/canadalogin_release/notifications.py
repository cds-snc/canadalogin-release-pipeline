from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .config import ConfigError, PipelineConfig, ValueReference
from .runtime import RuntimeContext, resolve_reference

STATUS_DETAILS = {
    "deploy-start": (":hourglass:", "is being deployed to", "info"),
    "deploy-success": (":white_check_mark:", "was successfully deployed to", "info"),
    "deploy-failure": (":x:", "failed to deploy to", "alert"),
    "build-failure": (":x:", "failed to build for", "alert"),
    "pipeline-failure": (":x:", "release pipeline failed for", "alert"),
}


@dataclass(frozen=True)
class NotificationResult:
    delivered: int
    skipped: bool


def notify(
    config: PipelineConfig,
    status: str,
    context: RuntimeContext,
    *,
    workflow_url: str,
    detail: str = "",
    sender: Callable[[str, bytes], None] | None = None,
) -> NotificationResult:
    try:
        icon, action, channel = STATUS_DETAILS[status]
    except KeyError as error:
        raise ConfigError(f"Unknown notification status {status!r}") from error

    references: tuple[ValueReference, ...]
    if channel == "info":
        references = (
            (config.notifications.info_webhook,)
            if config.notifications.info_webhook is not None
            else ()
        )
    else:
        references = config.notifications.alert_webhooks
    if not references:
        print(f"No {channel} Slack webhook is configured; skipping notification.")
        return NotificationResult(delivered=0, skipped=True)

    suffix = f" ({detail})" if detail else ""
    text = (
        f"{icon} {config.application} {action} `{context.environment}`{suffix}.\n\n"
        f"<{workflow_url}|View the release pipeline run>"
    )
    body = json.dumps({"text": text}, separators=(",", ":")).encode()
    send = sender or _send
    for reference in references:
        send(resolve_reference(reference, context), body)
    return NotificationResult(delivered=len(references), skipped=False)


def notify_pipeline_failure(
    application: str,
    workflow_url: str,
    webhooks: Sequence[str],
    *,
    detail: str = "planning or release-please",
    sender: Callable[[str, bytes], None] | None = None,
) -> NotificationResult:
    unique_webhooks = tuple(dict.fromkeys(webhook for webhook in webhooks if webhook))
    if not unique_webhooks:
        print("No Slack alert webhook is configured; skipping pipeline notification.")
        return NotificationResult(delivered=0, skipped=True)
    text = (
        f":x: {application} release pipeline failed during {detail}.\n\n"
        f"<{workflow_url}|View the release pipeline run>"
    )
    body = json.dumps({"text": text}, separators=(",", ":")).encode()
    send = sender or _send
    for webhook in unique_webhooks:
        send(webhook, body)
    return NotificationResult(delivered=len(unique_webhooks), skipped=False)


def _send(webhook_url: str, body: bytes) -> None:
    request = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if not 200 <= response.status < 300:
            raise ConfigError(f"Slack webhook returned HTTP {response.status}")
