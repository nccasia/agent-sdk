"""Metacognition capability — an OPT-IN plugin that makes the agent able to think about
its own thinking.

The reframe (``docs/concepts/11-metacognition.md``): metacognition is not a watcher bolted
above the agent — it is a **faculty you grant**. The always-on deterministic kernel
(``monitor → regulate``) stays the floor; this plugin adds the *capacity surface* on top:

- ``lobes.py``  — ``meta_context`` lobe: the mirror that renders the turn's own thinking
  state into the prompt (path/flow/skills/observations/flow-bias).
- ``tool.py``   — the single ``meta_control`` tool: the agent CALLS it to reshape thinking;
  it WRITES a decision; deterministic enactors READ + apply it (reason → write → enact).
- ``stages.py`` — ``meta_reflect`` (reflect/regulate step) → ``meta_fanout`` (the meta-decided
  work-list) → ``synthesize``; plus the opt-in ``meta`` flow.
- ``path.py``   — the conservative ``meta`` recognizer + the next-turn flow-bias signal.

Composes onto a subagent too: a fan-out item may carry ``lobes=["meta_context", …]`` /
``tools=["meta_control", …]``, so a subagent borrows the globally-installed faculty and gains
its own think-about-thinking. (Honest gap: it *borrows* the global install — it cannot
``install`` a plugin only it sees; true per-subagent capacity scoping is still a to-build.)

Opt-in (not in ``default_capability_plugins``): mount ``plugins=[MetacognitionPlugin()]`` to
add it, drop it to remove every meta factor. A no-plugin agent is byte-identical to the
default network (parity invariant 2). ``cite``/``filter`` stay pinned — never a meta decision.
"""

from __future__ import annotations

from agent_sdk.plugins.base import AgentSetup
from agent_sdk.plugins.metacognition.lobes import LOBE as META_CONTEXT_LOBE
from agent_sdk.plugins.metacognition.stages import meta_flow, meta_stages
from agent_sdk.plugins.metacognition.tool import MetaControlToolRuntime

__all__ = ["MetacognitionPlugin", "MetaControlToolRuntime", "META_CONTEXT_LOBE"]


class MetacognitionPlugin:
    """Opt-in metacognition faculty: meta-context lobe + reflect/fan-out stages + the
    ``meta_control`` tool (+ an optional ``meta`` flow)."""

    name = "metacognition"

    def __init__(
        self, *, flow: bool = True, subagents: object | None = None, auto_delegate: bool = False
    ):
        # ``flow=False`` contributes only the lobe + stages + tool (compose ``meta_reflect``
        # into your own flow) without registering the standalone ``meta`` flow/recognizer.
        # ``subagents`` is an optional ``SubagentRegistry`` — pass it to let ``fan_out`` items
        # delegate to named subagents (resolved deterministically by the enactor).
        # ``auto_delegate`` adds the deterministic complexity signal so the agent reflects-then-
        # fans-out on complex queries without an explicit cue (off ⇒ conservative cue-only).
        self._flow = flow
        self._subagents = subagents
        self._auto_delegate = auto_delegate

    def lobes(self) -> list:
        return [META_CONTEXT_LOBE]

    def install(self, setup: AgentSetup) -> None:
        setup.add_lobe(META_CONTEXT_LOBE)
        # A stateful runtime mounted whole (priority-composed ahead of @tool fns).
        setup.add_tool_runtime(MetaControlToolRuntime(registry=self._subagents))
        for st in meta_stages():
            setup.add_stage(st)
        if self._flow:
            from agent_sdk.plugins.metacognition.path import make_recognize

            setup.add_flow(meta_flow(make_recognize(auto_delegate=self._auto_delegate)))
