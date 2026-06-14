"""``PluginRegistry`` — manage a named set of plugins (enable / disable / override).

The control surface for plugins. Register builtin or custom plugins by ``name``, toggle them on
/off, override a builtin with your own, and resolve the **active** set the agent runs:

    from agent_sdk.plugins import PluginRegistry, builtin_registry, PluginMCP

    reg = builtin_registry()                 # the SDK builtins, registered
    reg.disable("otel")                      # turn one off
    reg.register(PluginMCP(spec={...}))      # add a configured plugin
    reg.override(MyWorkspace())              # replace the builtin "workspace"
    agent = PreactAgent(client=…, plugins=reg)   # PreactAgent uses reg.active()

A plugin is identified by its ``name``. Registration order is preserved; a re-register with the
same name **overrides** in place. A disabled name (or a plugin with ``enabled = False``) is
excluded from ``active()``.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

__all__ = ["PluginRegistry"]


class PluginRegistry:
    """A named, toggle-able set of plugins. Pass it to ``PreactAgent(plugins=…)``."""

    def __init__(self, plugins: Iterable[Any] | None = None):
        self._plugins: dict[str, Any] = {}
        self._disabled: set[str] = set()
        for p in plugins or ():
            self.register(p)

    def register(self, plugin: Any) -> PluginRegistry:
        """Add a plugin (or **override** one with the same ``name``). Re-enables the name."""
        name = getattr(plugin, "name", None) or type(plugin).__name__
        self._plugins[name] = plugin
        self._disabled.discard(name)
        return self

    # An override is just a re-register by name — kept as an explicit, readable alias.
    override = register

    def remove(self, name: str) -> PluginRegistry:
        self._plugins.pop(name, None)
        self._disabled.discard(name)
        return self

    def enable(self, name: str) -> PluginRegistry:
        self._disabled.discard(name)
        return self

    def disable(self, name: str) -> PluginRegistry:
        self._disabled.add(name)
        return self

    def is_enabled(self, name: str) -> bool:
        p = self._plugins.get(name)
        return p is not None and name not in self._disabled and getattr(p, "enabled", True)

    def is_disabled(self, name: str) -> bool:
        """Whether ``name`` was explicitly turned off (``disable(name)``) — lets a
        default-on capability plugin (e.g. ``disable("rag")``) be suppressed even
        when it was never registered on this registry."""
        return name in self._disabled

    def get(self, name: str) -> Any | None:
        return self._plugins.get(name)

    def names(self) -> list[str]:
        return list(self._plugins)

    def active(self) -> list[Any]:
        """The enabled plugins, in registration order — what the agent installs."""
        return [p for n, p in self._plugins.items() if self.is_enabled(n)]

    def __iter__(self) -> Iterator[Any]:
        return iter(self.active())

    def __len__(self) -> int:
        return len(self.active())

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        on = [n for n in self._plugins if self.is_enabled(n)]
        off = [n for n in self._plugins if not self.is_enabled(n)]
        return f"PluginRegistry(enabled={on}, disabled={off})"
