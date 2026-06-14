"""The single ``meta_control`` tool — the agent's meta-control surface.

The agent CALLS it to reshape *how* it is thinking. Each action follows the live
*reason → write → enact* pattern: the tool **writes** a meta-decision into turn state; a
**deterministic enactor reads + applies** it — the object level is never asked to judge
itself inline. Levers (the doc's table):

- ``use_skills`` → ``lobe_outputs["skills_in_use"]`` (the existing ``skill_active`` lobe
  reads + drives it — the reused enactor).
- ``bias_flow``  → ``lobe_outputs["meta_flow_bias"]`` (persisted to the session, read as a
  deterministic signal on the NEXT turn — flow is resolved once at turn start).
- ``regulate``   → ``scratchpad["meta_regulate_request"]`` (the engine seam honors
  ``trim``/``skip``, gated by the apply allow-list; a step carrying a pinned lobe
  (``cite``/``filter``) is never skippable. Deterministic ``retry_step`` stays the
  kernel regulator's domain — the tool does not request it.).
- ``navigate``  → ``scratchpad["nav_request"]`` + ``scratchpad["phase_brief"]`` (the
  **Navigator** layer): at a phase boundary the model decides "are we good to go?" and
  "what next?" — ``to`` is ``next``/``redo``/``done``/a phase id — and may author the next
  phase's brief (``goal``/``instruction``/``dod``). The engine's movable phase cursor
  enacts it (apply-gated + budgeted; pinned ``cite``/``filter`` always run). The
  ``nav_brief`` lobe renders the brief into the target phase's prompt.

``cite``/``filter`` are pinned (``PINNED_UNSKIPPABLE``) and stripped from any skill or
regulate request defensively — ground-or-refuse is not a meta decision.
"""

from __future__ import annotations

from agent_sdk.metacognition_facade import PINNED_UNSKIPPABLE

__all__ = ["MetaControlToolRuntime", "NAV_REQUEST_KEY", "PHASE_BRIEF_KEY"]

_ACTIONS = ("use_skills", "bias_flow", "regulate", "navigate")
_REGULATE_REQUESTS = ("trim", "skip")
NAV_REQUEST_KEY = "nav_request"
PHASE_BRIEF_KEY = "phase_brief"


class MetaControlToolRuntime:
    """A ``ToolRuntime`` exposing the single ``meta_control`` tool over turn state.

    Fan-out/delegation is NOT here — that is the dedicated subagents module's ``Subagent``
    tool. This tool only reshapes the current approach: pick skills, bias the flow, regulate.
    """

    name = "metacognition"

    def get_tool_specs(self) -> list[dict]:
        return [
            {
                "name": "meta_control",
                "description": (
                    "Reshape HOW you approach this turn (metacognition). Read 'How you are "
                    "thinking' first. One tool, choose `action`:\n"
                    "- use_skills: drive specific skills {slugs: [slug, …]}\n"
                    "- bias_flow: prefer a flow/path {path: name} (applies to your NEXT turn)\n"
                    "- regulate: request {request: trim|skip, step?: name} "
                    "(a grounding step (cite/filter) is never skippable)\n"
                    "- navigate: at a phase boundary, decide if the current phase is done and "
                    "what runs next {to: next|redo|done|<phase id>}. Optionally brief the next "
                    "phase: {goal, instruction, dod: [criteria]}. Use redo when a phase didn't "
                    "meet its goal; goto a phase id to run the right one next.\n"
                    "Call this only when the default approach is wrong — not every turn."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": list(_ACTIONS)},
                        "slugs": {"type": "array", "items": {"type": "string"}},
                        "path": {"type": "string"},
                        "request": {"type": "string", "enum": list(_REGULATE_REQUESTS)},
                        "step": {"type": "string"},
                        "to": {"type": "string"},
                        "goal": {"type": "string"},
                        "instruction": {"type": "string"},
                        "dod": {"type": "array", "items": {"type": "string"}},
                        "reason": {"type": "string"},
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
        if action == "regulate":
            return self._regulate(turn, inp)
        if action == "navigate":
            return self._navigate(turn, inp)
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

    def _navigate(self, turn, inp: dict) -> str:
        """Navigator: route the phase cursor (next/redo/done/<id>) + optionally brief the
        target phase (goal/instruction/dod). Writes turn state; the engine's movable cursor
        enacts it at the phase boundary (apply-gated + budgeted)."""
        to = str(inp.get("to") or "next").strip()
        if to in PINNED_UNSKIPPABLE:
            return f"Refused: '{to}' is a pinned grounding step and is not a navigation target."
        sp = getattr(turn, "scratchpad", None)
        if sp is None:
            return "Error: no scratchpad — cannot record a navigation request."
        sp.set(NAV_REQUEST_KEY, {"to": to, "reason": str(inp.get("reason") or "navigate")})
        # Optional brief for the next/target phase — keyed by phase id so nav_brief renders
        # it when that phase runs. For redo/next without an explicit id, key it under "next".
        goal = str(inp.get("goal") or "").strip()
        instruction = str(inp.get("instruction") or "").strip()
        dod = [str(d) for d in (inp.get("dod") or []) if str(d)]
        if goal or instruction or dod:
            briefs = sp.get(PHASE_BRIEF_KEY)
            if not isinstance(briefs, dict):
                briefs = {}
            key = to if to not in ("next", "redo", "done") else "next"
            briefs[key] = {"goal": goal, "instruction": instruction, "dod": dod}
            sp.set(PHASE_BRIEF_KEY, briefs)
        brief_note = " (+brief)" if (goal or instruction or dod) else ""
        return (
            f"Navigation recorded: → {to}{brief_note} (applied at the phase boundary if the "
            "apply allow-list permits; cite/filter always run)."
        )
