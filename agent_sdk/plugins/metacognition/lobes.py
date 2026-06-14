"""Metacognition lobe (OY axis) — the mirror: render the agent's own thinking state.

``meta_context`` surfaces *how the turn is being approached* — the recognized path, the
current stage, the active lobes, the skills in use, the deterministic observations the
kernel monitor produced this step, and any flow bias recorded for the next turn. The agent
cannot reason about its approach if it cannot see it; this lobe is that mirror (the
inspection snapshot exists today but never enters the prompt — this is where it does, but
only when the plugin is installed).

It is the *read/render* half of the capability (the ``meta_control`` tool is the *reshape*
half); tunable on its own like any lobe. Contributes nothing when there is no thinking
state to show (harmless wherever it is listed).
"""

from __future__ import annotations

from agent_sdk.contracts.turn import PromptContribution, TurnContext
from agent_sdk.lobes.runtime import Lobe
from agent_sdk.network.activation import LAYER_COGNITION

__all__ = ["MetaContextLobe", "LOBE"]


def _as_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if str(v)]
    return []


class MetaContextLobe(Lobe):
    """Render a 'how you are thinking' block from the turn's own reasoning state."""

    id = "meta_context"
    name = "Meta Context"
    description = (
        "Renders the agent's own thinking state (path/flow/skills/observations) into context."
    )
    use_when = "the agent should reflect on how it is approaching the task before reshaping it"
    how = (
        "reads the recognized path, current stage, active lobes, skills in use, the kernel's "
        "observations this step, and any recorded flow bias, and renders them as a 'how you are "
        "thinking' block — the mirror the meta_control tool reasons against"
    )
    layer = LAYER_COGNITION  # meta thinking sits above the object-level work
    behavior = "select"
    prior = 1.0  # active wherever a meta stage lists it; prompt() is empty without state

    def activation(self, ctx: dict) -> float:
        return 1.0

    def prompt(self, ctx: TurnContext) -> list[PromptContribution]:
        outs = getattr(ctx, "lobe_outputs", {}) or {}
        lines: list[str] = []

        path = getattr(ctx, "active_path", None)
        if path:
            lines.append(f"- Path (recognized intent): {path}")
        stage = getattr(ctx, "stage_id", None)
        if stage:
            lines.append(f"- Current step: {stage}")
        active = _as_list(getattr(ctx, "active_lobes", ()) or ())
        if active:
            lines.append(f"- Active lobes: {', '.join(sorted(active))}")
        skills = _as_list(outs.get("skills_in_use"))
        if skills:
            lines.append(f"- Skills in use: {', '.join(skills)}")

        observations = outs.get("meta_observations")
        if isinstance(observations, (list, tuple)) and observations:
            obs_txt = "; ".join(
                f"{o.get('kind')} @ {o.get('target')}" for o in observations if isinstance(o, dict)
            )
            if obs_txt:
                lines.append(f"- Observations this step: {obs_txt}")

        bias = outs.get("meta_flow_bias")
        if bias:
            lines.append(
                f"- Flow bias recorded: '{bias}' (applies to your NEXT turn, not this one)"
            )

        if not lines:
            return []

        block = (
            "## How you are thinking\n"
            "This is your own reasoning state for this turn. If the default approach is wrong, "
            "reshape it with the meta_control tool (pick skills / bias the flow / fan out / "
            "request regulation).\n" + "\n".join(lines)
        )
        return [PromptContribution(block, stability="volatile", source=self.id)]


LOBE = MetaContextLobe()
