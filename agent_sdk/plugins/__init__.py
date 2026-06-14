"""Plugins — the single, composable extension mechanism (``plugins=[…]``).

A plugin is a *first-class plug-and-play component*: at assembly time it may contribute the
**full capacity surface** — lobes, stages, paths/flows, skills, and tools — plus event hooks,
guardrails, MCP servers, and seam bindings. A plugin may also subtract a builtin it owns via
``setup.remove_*`` (pinned lobes always survive).

The **core network** lives in ``agent_sdk/lobes/`` (cognition, tools, skills, task, memory, the
reply flow) — intrinsic to every agent, not plugins. Plugins are the *extension* layer on top:
the default-on but toggleable ``safety/`` (``SafetyPlugin`` — ``cite``/``filter`` grounding) and
``format/`` (``FormatPlugin`` — output styling), plus the opt-in integrations.

**Folder per plugin.** Each builtin lives in its own subpackage so it can own its code and be
managed independently: ``safety/`` (``SafetyPlugin``), ``format/`` (``FormatPlugin``), ``mcp/``
(``PluginMCP``), ``workspace/`` (``PluginWorkspace`` + FS drivers), ``otel/`` (``PluginOTel``),
``guardrails/`` (``PluginGuardrails``), ``support_triage/`` (``PluginSupportTriage`` — a worked
example carrying every capacity kind).

**Enable / disable / override** via :class:`PluginRegistry`: register builtin or custom plugins
by name, toggle them, override a builtin with your own, and pass the registry straight to
``PreactAgent(plugins=…)`` (it installs ``registry.active()``). ``builtin_registry()`` returns a
registry pre-loaded with the no-config builtins.
"""

from __future__ import annotations

from agent_sdk.mcp import MCPError, MCPServerSpec, MCPToolRuntime
from agent_sdk.plugins.base import AgentSetup, Plugin, Workspace
from agent_sdk.plugins.format import FormatPlugin
from agent_sdk.plugins.guardrails import (
    GuardrailError,
    PluginGuardrails,
    make_answer_leak_check,
)
from agent_sdk.plugins.mcp import (
    HTTPMCPToolRuntime,
    PluginMCP,
    activation_matches,
    select_active,
)
from agent_sdk.plugins.metacognition import MetacognitionPlugin
from agent_sdk.plugins.otel import PluginOTel
from agent_sdk.plugins.rag import RagPlugin
from agent_sdk.plugins.registry import PluginRegistry
from agent_sdk.plugins.safety import SafetyPlugin
from agent_sdk.plugins.support_triage import PluginSupportTriage
from agent_sdk.plugins.tasks import TaskPlugin
from agent_sdk.plugins.workspace import (
    FsToolRuntime,
    LocalWorkspace,
    PluginWorkspace,
    S3Workspace,
    VirtualWorkspace,
)


def default_capability_plugins() -> list:
    """The *default-on but toggleable* extensions: :class:`SafetyPlugin` (the ``filter``
    output-safety lobe — every agent wants it) and :class:`FormatPlugin` (``format`` styling).
    Their lobes round out the production network on top of the core (``lobes/_core_lobe_objects``).
    **Retrieval grounding is NOT here** — :class:`RagPlugin` (``cite`` + the citation contract)
    is opt-in, since most agents have no retrieval; plug it in explicitly or via
    ``require_citations=True``. The intrinsic lobes — cognition, tools, skills, task, memory,
    reply — are core, not plugins. Disable either via a :class:`PluginRegistry`."""
    return [SafetyPlugin(), FormatPlugin()]


def capability_lobes() -> list:
    """Every lobe owned by the default-on extension plugins (flattened) — woven onto the core
    network by ``lobes/network.py`` (the engine re-sorts to canonical ``(layer, order)``)."""
    return [lb for plugin in default_capability_plugins() for lb in plugin.lobes()]


def builtin_registry() -> PluginRegistry:
    """A :class:`PluginRegistry` pre-loaded with the no-config builtin plugins (observability +
    guardrails, both no-ops until configured). Register configured ``PluginMCP`` /
    ``PluginWorkspace`` / ``PluginSupportTriage`` (or your own) on it, then ``.enable``/
    ``.disable``/``.override`` as needed and pass it to ``PreactAgent(plugins=…)``."""
    return PluginRegistry([PluginOTel(), PluginGuardrails()])


__all__ = [
    "Plugin",
    "AgentSetup",
    "Workspace",
    "PluginRegistry",
    "builtin_registry",
    "default_capability_plugins",
    "capability_lobes",
    "RagPlugin",
    "SafetyPlugin",
    "TaskPlugin",
    "MetacognitionPlugin",
    "FormatPlugin",
    "PluginWorkspace",
    "PluginMCP",
    "select_active",
    "activation_matches",
    "PluginOTel",
    "PluginGuardrails",
    "make_answer_leak_check",
    "PluginSupportTriage",
    "MCPToolRuntime",
    "MCPServerSpec",
    "MCPError",
    "GuardrailError",
    "VirtualWorkspace",
    "LocalWorkspace",
    "S3Workspace",
    "FsToolRuntime",
    "HTTPMCPToolRuntime",
]
