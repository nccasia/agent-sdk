"""Shared helpers for the two skill lobes (skill_select + skill_active).

A skill is a workflow the model is driven through. The lifecycle splits across
two lobes by STATE: skill_select owns the NON-SELECTED states (listing/selecting/
activating — surface the index, cue selection), skill_active owns the SELECTED
states (activated/driving — inject the active skill + drive its steps). Both read
the same skill-state flag vocabulary, computed here once.
"""

from __future__ import annotations

from agent_sdk.lobes.runtime import TurnContext


def prompt_block(
    registry,
    policy: dict,
    stage_id: str,
    *,
    query: str | None = None,
    q_vec=None,
    embed_one=None,
    ranking_out: list | None = None,
    active_slugs: list[str] | None = None,
    skills_in_use: list[str] | None = None,
) -> str:
    """Visible skill prompt block for a stage (adaptive on_demand list when
    ``skill_strategy == "adaptive"``; omitted scoring inputs ⇒ legacy full list).
    ``skills_in_use`` drops in-use skills from the index (state-aware)."""
    from agent_sdk.skills import build_skill_prompt_block

    return build_skill_prompt_block(
        registry,
        policy,
        stage_id,
        query=query,
        q_vec=q_vec,
        embed_one=embed_one,
        ranking_out=ranking_out,
        active_slugs=active_slugs,
        skills_in_use=skills_in_use,
    )


def active_skill_packs(ctx: TurnContext) -> list:
    """Resolve the active skill packs from the TurnContext. ``[]`` = no skills."""
    if not getattr(ctx, "lobe_outputs", None):
        return []
    registry = ctx.lobe_outputs.get("skill_registry")
    if registry is not None and ctx.stage_id is not None:
        try:
            return list(registry.active_for_stage(dict(ctx.policy), ctx.stage_id))
        except Exception:
            pass
    return list(ctx.lobe_outputs.get("active_skills") or [])


def skill_flags(ctx: TurnContext) -> dict[str, float]:
    """The deterministic skill-state flag vocabulary both skill lobes read:

    - skills_declared:    at least one active skill
    - skills_unselected:  declared but none in use yet (SELECTING)
    - skills_in_use:      at least one skill activated this turn (DRIVING)
    - has_read_directive: an active skill declares a read directive (ACTIVATING)
    """
    skills = active_skill_packs(ctx)
    in_use = bool(ctx.lobe_outputs.get("skills_in_use"))
    return {
        "skills_declared": 1.0 if skills else 0.0,
        "skills_unselected": 1.0 if (skills and not in_use) else 0.0,
        "skills_in_use": 1.0 if in_use else 0.0,
        "has_read_directive": 1.0
        if any(getattr(s, "read_directive", None) or getattr(s, "has_read", False) for s in skills)
        else 0.0,
    }
