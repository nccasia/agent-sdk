"""Metacognition stages (OX axis) + the optional ``meta`` flow.

``meta_reflect`` (a deliberate reflect/regulate step: consult the meta-context mirror and
reshape the approach via ``meta_control`` — do NOT answer) → ``synthesize`` (the core grounded
stage: classify/synthesize/cite/filter).

The reflect stage is also a reusable unit: list ``meta_reflect`` in your own flow's stages
to add a reflect step without routing to the whole ``meta`` flow. A meta stage cannot be
auto-spliced into the default flows (flows are immutable stage-id lists), so the default
network is never mutated — you opt in by mounting the plugin or composing the stage.

Fan-out/delegation is NOT here — that is the dedicated subagents module (``Subagent`` tool +
``fanout``/``fanin`` stages). Metacognition only reshapes the current approach.
"""

from __future__ import annotations

from agent_sdk.flow_def import flow
from agent_sdk.plugins.metacognition.path import recognize
from agent_sdk.stages import stage

__all__ = ["meta_stages", "meta_flow"]

_REFLECT_PROMPT = (
    "REFLECT, then reshape your approach if needed — do NOT answer the task in this step. "
    "Read 'How you are thinking' above, then decide:\n"
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
            lobes=["meta_context", "synthesize"],
            loop="agentic",
            tools=["meta_control"],
            hops=4,
            description="Reflect on the approach and reshape it via meta_control (no answer yet).",
            system_prompt=_REFLECT_PROMPT,
        ),
    ]


def meta_flow():
    """The opt-in ``meta`` flow: reflect-then-answer (cue/bias recognized)."""
    return flow(
        "meta",
        use_when="reason about and reshape the approach before doing the task",
        stages=["meta_reflect", "synthesize"],
        grounds=True,
        threshold=0.5,
        signal=recognize,
    )
