"""Metacognition stages (OX axis) + the optional ``meta`` flow.

``meta_reflect`` (a deliberate reflect/regulate step: consult the meta-context mirror and
reshape the approach via ``meta_control`` — do NOT answer) → ``meta_fanout`` (the engine's
generic ``loop="map"`` runs the meta-decided work-list, one scoped sub-execution per item;
degrades to a single run when nothing was fanned out) → ``synthesize`` (the core grounded
stage: classify/synthesize/cite/filter).

The reflect stage is also a reusable unit: list ``meta_reflect`` in your own flow's stages
to add a reflect step without routing to the whole ``meta`` flow. A meta stage cannot be
auto-spliced into the default flows (flows are immutable stage-id lists), so the default
network is never mutated — you opt in by mounting the plugin or composing the stage.
"""

from __future__ import annotations

from agent_sdk.flow_def import flow
from agent_sdk.plugins.metacognition.path import recognize
from agent_sdk.stages import stage

__all__ = ["meta_stages", "meta_flow", "FANOUT_KEY"]

FANOUT_KEY = "meta_fanout"

_REFLECT_PROMPT = (
    "REFLECT, then reshape your approach if needed — do NOT answer the task in this step. "
    "Read 'How you are thinking' above, then decide:\n"
    "- If the task has two or more independent parts, call meta_control action=fan_out with one "
    "item per part (each {label, input}).\n"
    "- If it needs a specialized procedure available to you as a skill, call meta_control "
    "action=use_skills with the slug(s).\n"
    "- If the user signals a lasting change of approach for future turns, call meta_control "
    "action=bias_flow.\n"
    "- Otherwise do nothing — the normal pipeline will answer.\n"
    "Reshape at most once; then stop (the next steps carry out the work)."
)


def meta_stages() -> list:
    return [
        stage(
            "meta_reflect",
            # ``subagent_catalog`` is an OPTIONAL lobe: it renders the named subagents the
            # agent may delegate to, but only when the subagents plugin registers it. An
            # unregistered lobe id is silently skipped (engine_context.collect_nodes), so
            # listing it here is harmless to a bare metacognition install.
            lobes=["meta_context", "subagent_catalog", "synthesize"],
            loop="agentic",
            tools=["meta_control"],
            hops=4,
            description="Reflect on the approach and reshape it via meta_control (no answer yet).",
            system_prompt=_REFLECT_PROMPT,
        ),
        # Generic per-item driver: the engine fans out over scratchpad[FANOUT_KEY] (filled by
        # meta_control fan_out), one scoped sub-execution per item with its own spec. Each item
        # may carry its own lobes/tools — including meta_context/meta_control — so a subagent
        # gains its OWN meta faculty (per-subagent capacity scoping). Empty ⇒ single run (parity).
        stage(
            "meta_fanout",
            lobes=["meta_context", "synthesize"],
            loop="map",
            fanout_key=FANOUT_KEY,
            fanout_parallel=True,
            fanout_isolated=True,
            hops=12,
            description="Run the meta-decided work-list: one isolated, parallel sub-execution "
            "per item (each subagent gets its own evidence pool; only its memo returns).",
        ),
    ]


def meta_flow():
    return flow(
        "meta",
        use_when="reason about and reshape the approach before doing the task",
        stages=["meta_reflect", "meta_fanout", "synthesize"],
        grounds=True,
        threshold=0.5,
        signal=recognize,
    )
