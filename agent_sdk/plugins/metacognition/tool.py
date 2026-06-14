"""The single ``meta_control`` tool — the agent's meta-control surface.

The agent CALLS it to reshape *how* it is thinking. Each action follows the live
*reason → write → enact* pattern: the tool **writes** a meta-decision into turn state; a
**deterministic enactor reads + applies** it — the object level is never asked to judge
itself inline. Levers (the doc's table):

- ``use_skills`` → ``lobe_outputs["skills_in_use"]`` (the existing ``skill_active`` lobe
  reads + drives it — the reused enactor).
- ``bias_flow``  → ``lobe_outputs["meta_flow_bias"]`` (persisted to the session, read as a
  deterministic signal on the NEXT turn — flow is resolved once at turn start).
- ``fan_out``    → ``scratchpad["meta_fanout"]`` (the ``meta_fanout`` map stage runs one
  scoped sub-execution per item; an item may carry its own ``lobes``/``tools`` — per-
  subagent capacity scoping).
- ``regulate``   → ``scratchpad["meta_regulate_request"]`` (the engine seam honors
  ``trim``/``skip``, gated by the apply allow-list; a step carrying a pinned lobe
  (``cite``/``filter``) is never skippable. Deterministic ``retry_step`` stays the
  kernel regulator's domain — the tool does not request it.).

``cite``/``filter`` are pinned (``PINNED_UNSKIPPABLE``) and stripped from any skill or
regulate request defensively — ground-or-refuse is not a meta decision.
"""

from __future__ import annotations

from agent_sdk.metacognition_facade import PINNED_UNSKIPPABLE

__all__ = ["MetaControlToolRuntime"]

_ACTIONS = ("use_skills", "bias_flow", "fan_out", "regulate")
_REGULATE_REQUESTS = ("trim", "skip")
_FANOUT_KEY = "meta_fanout"


class MetaControlToolRuntime:
    """A ``ToolRuntime`` exposing the single ``meta_control`` tool over turn state.

    An optional ``SubagentRegistry`` lets ``fan_out`` items name a reusable subagent
    (``{"agent": "reviewer", "input": "…"}``) — the deterministic resolution of Claude
    Code's "manual invocation by name". With no registry, items pass through unchanged
    (an ``agent`` key is just an inert field), so the faculty works standalone.
    """

    name = "metacognition"

    def __init__(self, registry: object | None = None):
        # ``SubagentRegistry``-shaped: ``.resolve_item(dict) -> dict`` (raises KeyError on an
        # unknown name). Kept duck-typed so this module needn't import the subagents package.
        self._registry = registry

    def get_tool_specs(self) -> list[dict]:
        return [
            {
                "name": "meta_control",
                "description": (
                    "Reshape HOW you approach this turn (metacognition). Read 'How you are "
                    "thinking' first. One tool, choose `action`:\n"
                    "- use_skills: drive specific skills {slugs: [slug, …]}\n"
                    "- bias_flow: prefer a flow/path {path: name} (applies to your NEXT turn)\n"
                    "- fan_out: split into parallel sub-tasks {items: [{label, input, "
                    "agent?, lobes?, tools?, system_prompt?, model?, max_tokens?, hops?}]} "
                    "(set `agent` to delegate to a named subagent)\n"
                    "- regulate: request {request: trim|skip, step?: name} "
                    "(a grounding step (cite/filter) is never skippable)\n"
                    "Call this only when the default approach is wrong — not every turn."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": list(_ACTIONS)},
                        "slugs": {"type": "array", "items": {"type": "string"}},
                        "path": {"type": "string"},
                        "items": {"type": "array", "items": {"type": "object"}},
                        "request": {"type": "string", "enum": list(_REGULATE_REQUESTS)},
                        "step": {"type": "string"},
                    },
                    "required": ["action"],
                },
            }
        ]

    async def call_tool(
        self, name: str, inp: dict, retrieved_chunks=None, already_read=None
    ) -> str:
        if name != "meta_control":
            return f"Error: unknown tool {name!r}."
        turn = self._turn()
        if turn is None:
            return "Error: no active turn — meta_control must be called inside a turn."
        action = str(inp.get("action") or "").lower()
        if action == "use_skills":
            return self._use_skills(turn, inp)
        if action == "bias_flow":
            return self._bias_flow(turn, inp)
        if action == "fan_out":
            return self._fan_out(turn, inp)
        if action == "regulate":
            return self._regulate(turn, inp)
        return f"Error: unknown action {action!r}. Use one of {', '.join(_ACTIONS)}."

    # ── seam ────────────────────────────────────────────────────────────────────
    @staticmethod
    def _turn():
        from agent_sdk.engine import current_turn

        return current_turn()

    # ── enactors ──────────────────────────────────────────────────────────────────
    def _use_skills(self, turn, inp: dict) -> str:
        outs = turn.lobe_outputs
        requested = [str(s) for s in (inp.get("slugs") or []) if str(s)]
        # Pin guard: cite/filter are not skills and are never reshapeable.
        slugs = [s for s in requested if s not in PINNED_UNSKIPPABLE]
        if not slugs:
            return "Error: use_skills requires 'slugs': [slug, …] (cite/filter are not skills)."
        in_use = outs.setdefault("skills_in_use", [])
        if not isinstance(in_use, list):
            in_use = []
            outs["skills_in_use"] = in_use
        for s in slugs:
            if s not in in_use:
                in_use.append(s)
        return f"Skills in use: {', '.join(in_use)}. Drive them to completion."

    def _bias_flow(self, turn, inp: dict) -> str:
        path = str(inp.get("path") or "").strip()
        if not path:
            return "Error: bias_flow requires 'path': flow/path name."
        turn.lobe_outputs["meta_flow_bias"] = path
        return (
            f"Flow bias '{path}' recorded — it applies to your NEXT turn (this turn's flow is "
            "already chosen). Answer this turn normally."
        )

    def _fan_out(self, turn, inp: dict) -> str:
        raw = inp.get("items") or []
        items: list[dict] = []
        for it in raw:
            if isinstance(it, dict) and (it.get("input") or it.get("label") or it.get("agent")):
                items.append(it)
        if not items:
            return "Error: fan_out requires 'items': [{label, input, …}]."
        # Resolve any named subagents against the registry (deterministic name → spec). An
        # unknown name fails the whole call with a clear message — never a silent pass.
        if self._registry is not None:
            resolved: list[dict] = []
            for it in items:
                try:
                    resolved.append(self._registry.resolve_item(it))
                except KeyError as exc:
                    known = ", ".join(getattr(self._registry, "names", lambda: [])()) or "(none)"
                    return f"Error: unknown subagent {exc.args[0]!r}. Known subagents: {known}."
            items = resolved
        sp = getattr(turn, "scratchpad", None)
        if sp is None:
            return "Error: no scratchpad — cannot record a fan-out work-list."
        sp.set(_FANOUT_KEY, items)
        labels = ", ".join(str(it.get("label") or it.get("input"))[:40] for it in items)
        return f"Recorded {len(items)} sub-task(s) for fan-out: {labels}."

    def _regulate(self, turn, inp: dict) -> str:
        request = str(inp.get("request") or "").lower()
        if request not in _REGULATE_REQUESTS:
            return f"Error: regulate requires 'request' in {', '.join(_REGULATE_REQUESTS)}."
        step = str(inp.get("step") or "").strip()
        if request == "skip" and step in PINNED_UNSKIPPABLE:
            return f"Refused: step '{step}' is pinned (cite/filter) and is never skippable."
        sp = getattr(turn, "scratchpad", None)
        if sp is None:
            return "Error: no scratchpad — cannot record a regulation request."
        sp.set("meta_regulate_request", {"request": request, "step": step or None})
        target = f" on step '{step}'" if step else ""
        return f"Regulation request recorded: {request}{target} (applied if the apply allow-list permits)."
