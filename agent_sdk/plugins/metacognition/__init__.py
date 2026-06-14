"""Metacognition capability — an OPT-IN plugin that makes the agent able to think about
its own thinking.

The reframe (``docs/concepts/11-metacognition.md``): metacognition is not a watcher bolted
above the agent — it is a **faculty you grant**. The always-on deterministic kernel
(``monitor → regulate``) stays the floor; this plugin adds the *capacity surface* on top:

- ``lobes.py``  — ``meta_context`` lobe: the mirror that renders the turn's own thinking
  state into the prompt (path/flow/skills/observations/flow-bias).
- ``tool.py``   — the single ``meta_control`` tool: the agent CALLS it to reshape thinking;
  it WRITES a decision; deterministic enactors READ + apply it (reason → write → enact).
- ``stages.py`` — ``meta_reflect`` (reflect/regulate step) → ``synthesize``; plus the opt-in
  ``meta`` flow.
- ``path.py``   — the conservative ``meta`` recognizer + the next-turn flow-bias signal.

Delegation/fan-out is a SEPARATE capability — the dedicated subagents module (the ``Subagent``
tool + ``fanout``/``fanin`` stages). Metacognition only reshapes the current approach.

Opt-in (not in ``default_capability_plugins``): mount ``plugins=[MetacognitionPlugin()]`` to
add it, drop it to remove every meta factor. A no-plugin agent is byte-identical to the
default network (parity invariant 2). ``cite``/``filter`` stay pinned — never a meta decision.
"""

from __future__ import annotations

from agent_sdk.plugins.base import AgentSetup
from agent_sdk.plugins.metacognition.lobes import LOBE as META_CONTEXT_LOBE
from agent_sdk.plugins.metacognition.lobes import LOBES as META_LOBES
from agent_sdk.plugins.metacognition.stages import meta_flow, meta_stages
from agent_sdk.plugins.metacognition.tool import MetaControlToolRuntime

__all__ = ["MetacognitionPlugin", "MetaControlToolRuntime", "META_CONTEXT_LOBE", "META_LOBES"]


class MetacognitionPlugin:
    """Opt-in metacognition faculty: meta-context lobe + reflect stage + the
    ``meta_control`` tool (+ an optional ``meta`` flow). Delegation/fan-out is a separate
    capability — the dedicated subagents module."""

    name = "metacognition"

    def __init__(self, *, flow: bool = True):
        # ``flow=False`` contributes only the lobe + stage + tool (compose ``meta_reflect``
        # into your own flow) without registering the standalone ``meta`` flow/recognizer.
        self._flow = flow

    def lobes(self) -> list:
        return list(META_LOBES)

    def install(self, setup: AgentSetup) -> None:
        for lobe in META_LOBES:  # meta_context (the mirror) + nav_brief (the Navigator brief)
            setup.add_lobe(lobe)
        # A stateful runtime mounted whole (priority-composed ahead of @tool fns).
        setup.add_tool_runtime(MetaControlToolRuntime())
        for st in meta_stages():
            setup.add_stage(st)
        if self._flow:
            setup.add_flow(meta_flow())
