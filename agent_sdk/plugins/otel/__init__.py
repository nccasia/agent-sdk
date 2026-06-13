"""``PluginOTel`` — OpenTelemetry-style observability via event hooks."""

from __future__ import annotations

import contextlib
from typing import Any

from agent_sdk.plugins.base import AgentSetup

__all__ = ["PluginOTel"]


class PluginOTel:
    name = "otel"

    def __init__(self, tracer: Any | None = None):
        self.tracer = tracer
        self.events: list[str] = []  # event type log (observable even without otel)

    def install(self, setup: AgentSetup) -> None:
        def hook(ev: Any) -> None:
            self.events.append(getattr(ev, "type", type(ev).__name__))
            if self.tracer is not None:
                with contextlib.suppress(Exception):
                    self.tracer.add_event(getattr(ev, "type", "event"), ev.to_json())

        setup.on_event(hook)
