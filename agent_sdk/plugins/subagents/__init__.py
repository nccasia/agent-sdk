"""Subagents capability — an OPT-IN plugin: named, reusable scoped workers + delegation.

Mount it to give the agent a registry of named subagents it can delegate to by name through
the metacognition ``meta_control(action=fan_out)`` enactor — Claude Code's "manual invocation
by name", resolved deterministically. The plugin:

- builds a :class:`~agent_sdk.subagents.SubagentRegistry` from in-code definitions/rows and/or
  ``.claude/agents/*.md`` files (``agents_dir``);
- installs the metacognition faculty wired with that registry (so ``fan_out`` items may carry
  ``agent: "<name>"``) — its ``meta_fanout`` stage runs workers parallel + context-isolated;
- contributes the ``subagent_catalog`` lobe so the reflect step sees the available subagents.

It composes the existing :class:`MetacognitionPlugin`; mount this OR ``MetacognitionPlugin``,
not both (both expose the ``meta_control`` tool). Opt-in — a no-plugin agent is byte-identical
to the default network (parity invariant 2). ``cite``/``filter`` stay pinned: a subagent's work
is aggregated, then grounded by the flow's pinned stages — grounding is never a worker's call.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agent_sdk.plugins.base import AgentSetup
from agent_sdk.plugins.metacognition import MetacognitionPlugin
from agent_sdk.plugins.subagents.lobes import SubagentCatalogLobe
from agent_sdk.subagents import Subagent, SubagentRegistry, load_agents_dir

__all__ = ["SubagentsPlugin", "SubagentCatalogLobe"]


class SubagentsPlugin:
    """Opt-in subagent fan-out: a named-subagent registry + deterministic delegation."""

    name = "subagents"

    def __init__(
        self,
        agents: Sequence[Subagent] | SubagentRegistry | None = None,
        *,
        rows: Sequence[Mapping[str, object]] | None = None,
        agents_dir: str | None = None,
        flow: bool = True,
    ):
        if isinstance(agents, SubagentRegistry):
            registry = agents
        else:
            registry = SubagentRegistry(list(agents or []))
        for row in rows or []:
            registry.add_row(row)
        for agent in load_agents_dir(agents_dir) if agents_dir else []:
            registry.register(agent)
        self.registry = registry
        self._flow = flow

    def install(self, setup: AgentSetup) -> None:
        # Delegate the meta faculty (lobe + reflect/fanout stages + meta_control tool),
        # wiring the registry so fan_out can resolve named subagents.
        MetacognitionPlugin(subagents=self.registry, flow=self._flow).install(setup)
        # Surface the catalog into the reflect step (the meta_reflect stage already lists
        # the optional ``subagent_catalog`` lobe id).
        setup.add_lobe(SubagentCatalogLobe(self.registry))
