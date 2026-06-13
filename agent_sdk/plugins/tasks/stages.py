"""Task stages (OX axis) — the pipeline that drives a checklist to completion.

``plan`` (build the rail via the todos tool) → ``execute`` (the engine's generic
``loop="map"`` runs ONE scoped sub-execution per todo, each with its own spec) →
``deliver`` (state the final answer). Each stage is a self-describing, independently
tunable unit (its own lobe slice / loop / tools / prompt).
"""

from __future__ import annotations

from agent_sdk.stages import stage

__all__ = ["task_stages"]

_PLAN_PROMPT = (
    "PLAN ONLY. Decompose the goal into an ordered checklist with the todos tool "
    "(action='add', title=…, deps=[ids] for steps that depend on earlier results). For a step "
    "that needs a focused setup, pass its own system_prompt/tools in the add call. Do NOT do the "
    "work yet — only build the checklist."
)
_DELIVER_PROMPT = (
    "The checklist is COMPLETE — every step's result is in the notes / checklist above. Do NOT run "
    "or plan more work, and do NOT say what you 'will' do. State the FINAL answer NOW: the concrete "
    "values and names the goal asks for, read directly from the step results. Output the answer only."
)


def task_stages() -> list:
    return [
        stage("plan", lobes=["synthesize"], loop="agentic", tools=["todos"], hops=8,
              description="Decompose the goal into an ordered todo checklist (the rail).",
              system_prompt=_PLAN_PROMPT),
        # Generic per-item driver: the engine fans out over the rail (scratchpad 'todos'),
        # running ONE scoped sub-execution per todo with its own spec. tools=() ⇒ each sub-agent
        # sees the full toolset (domain tools come from whatever other plugins are mounted).
        stage("execute", lobes=["synthesize", "task_rail"], loop="map", fanout_key="todos", hops=12,
              description="Drive the checklist: one scoped sub-execution per todo (per-todo spec)."),
        stage("deliver", lobes=["synthesize", "task_rail"], loop="single",
              description="Synthesize the final answer from the completed steps.",
              system_prompt=_DELIVER_PROMPT),
    ]
