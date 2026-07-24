"""The human back-channel. Pushes gate/escalation alerts so you can act while away.

A notification failure must NEVER break orchestration — every send is best-effort and
swallows its errors. NtfyNotifier posts to an ntfy topic and, when a public dashboard URL
is configured, attaches action buttons that POST the decision straight back, so a gate can
be answered from the phone without opening the dashboard.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Callable, Protocol

_MAX_ACTIONS = 3  # ntfy allows at most three action buttons


class Notifier(Protocol):
    def notify(self, kind: str, task_id: str, title: str, message: str, choices: list[str]) -> None:
        ...


class NullNotifier:
    """Default: no back-channel configured."""

    def notify(self, kind: str, task_id: str, title: str, message: str, choices: list[str]) -> None:
        return None


@dataclass
class NtfyNotifier:
    topic: str
    server: str = "https://ntfy.sh"
    dashboard_url: str | None = None
    sender: Callable[[urllib.request.Request], None] | None = None

    def notify(self, kind: str, task_id: str, title: str, message: str, choices: list[str]) -> None:
        try:
            self._publish(kind, task_id, title, message, choices)
        except Exception as exc:  # never let a notification break the pipeline
            print(f"[notify] send failed (non-fatal): {exc}")

    def _publish(self, kind: str, task_id: str, title: str, message: str, choices: list[str]) -> None:
        headers = {
            "Title": title,
            "Tags": "warning" if kind == "escalation" else "bell",
            "Priority": "high" if kind == "escalation" else "default",
        }
        actions = self._build_actions(kind, task_id, choices)
        if actions:
            headers["Actions"] = "; ".join(actions)
        request = urllib.request.Request(
            f"{self.server}/{self.topic}", data=message.encode("utf-8"),
            headers=headers, method="POST")
        (self.sender or _default_send)(request)

    def _build_actions(self, kind: str, task_id: str, choices: list[str]) -> list[str]:
        if not self.dashboard_url:
            return []
        endpoint = "resolve" if kind == "escalation" else "decide"
        field = "action" if kind == "escalation" else "choice"
        url = f"{self.dashboard_url}/api/tasks/{task_id}/{endpoint}"
        actions = [f"view, dashboard, {self.dashboard_url}"]
        for choice in choices[: _MAX_ACTIONS - 1]:
            body = json.dumps({field: choice})
            actions.append(
                f"http, {choice}, {url}, method=POST, "
                f"headers.Content-Type=application/json, body={body}")
        return actions


def _default_send(request: urllib.request.Request) -> None:
    urllib.request.urlopen(request, timeout=5).close()
