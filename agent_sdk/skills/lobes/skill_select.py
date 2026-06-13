"""skill_select — the NON-SELECTED skill states: surface the index and cue
selection.

WHAT   Show the bot's available skills and steer the model to pick + load the
       right one before answering (RFC 0013 progressive disclosure).
WHEN   Fires when the policy declares skills (`capabilities.skills` non-empty).
HOW    context → state → activation → prompt. :meth:`state` resolves the
       non-selected lifecycle (listing → selecting → activating); each state_node
       emits its piece. The SELECTED states (activated/driving) are the sibling
       `skill_active` lobe; the two coexist (list the rest while one drives).

         (none) ─▶ listing ─▶ selecting ─▶ activating          [skill_active: ─▶ driving]
                   skill:list  select:cue   read:hint
"""

from __future__ import annotations

from agent_sdk.lobes.runtime import Lobe, PromptContribution, TurnContext
from agent_sdk.network.activation import LAYER_SKILL, LobeNode
from agent_sdk.network.context_builder import ContextNode
from agent_sdk.skills.lobes._common import active_skill_packs, prompt_block, skill_flags

# Re-export for back-compat (callers import prompt_block from the select lobe).
prompt_block = prompt_block  # noqa: PLW0127


class SkillSelectLobe(Lobe):
    """The skill index + selection lobe (non-selected states)."""

    id = "skill_select"
    name = "Skill Select"
    description = "Surface the available skills and cue the model to pick + load one."
    use_when = "the bot declares skills and the turn could be handled by a skill workflow"
    how = (
        "Resolve the non-selected state and emit its piece: list the skills, cue "
        "selection when none is picked, and hint skill.read for progressive "
        "disclosure. The active skill body/drive is the sibling skill_active lobe."
    )
    system_prompt = None  # select lobe — emits the skill index block, not one template
    behavior = "select"
    layer = LAYER_SKILL
    order = 0
    writes = ("skill_pack",)

    def state(self, ctx: dict) -> str:
        if not ctx.get("skills_declared"):
            return ""
        if ctx.get("has_read_directive"):
            return "activating"
        if ctx.get("skills_unselected"):
            return "selecting"
        return "listing"

    def activation(self, ctx: dict) -> float:
        return 1.0 if ctx.get("skills_declared") else 0.0

    def prompt_block(self, registry, policy: dict, stage_id: str, *, _ctx=None,
                     query=None, q_vec=None, embed_one=None, ranking_out=None,
                     active_slugs=None) -> str:
        return prompt_block(registry, policy, stage_id, query=query, q_vec=q_vec,
                            embed_one=embed_one, ranking_out=ranking_out, active_slugs=active_slugs)

    def prompt(self, ctx: TurnContext) -> list[PromptContribution]:
        """Own the skill index for the NON-SELECTED states. State-aware: a skill
        already in use is dropped (skill_active drives it), so on a driving turn
        this collapses to nothing. Records the per-stage ranking into
        ``lobe_outputs['skill_ranking']`` for the inspector's skill_selection."""
        registry = ctx.lobe_outputs.get("skill_registry")
        ctx.lobe_outputs["skill_ranking"] = []  # reset per stage (skill_active turns ⇒ empty)
        if registry is None or ctx.stage_id is None:
            return []
        in_use = list(ctx.lobe_outputs.get("skills_in_use") or [])
        ranking: list = []
        block = prompt_block(
            registry, dict(ctx.policy), ctx.stage_id,
            query=getattr(ctx, "query", None), ranking_out=ranking, skills_in_use=in_use,
        )
        ctx.lobe_outputs["skill_ranking"] = ranking
        return [PromptContribution(block, stability="stable", source=self.id)] if block else []

    def _signal_ctx_for(self, ctx: TurnContext) -> dict:
        base = super()._signal_ctx_for(ctx)
        base.update(skill_flags(ctx))
        return base

    def state_machine(self) -> list[LobeNode]:
        # The index + selection directive are owned by :meth:`prompt` (one
        # renderer, state-aware), so the listing/select-cue produce nodes are gone
        # — they only duplicated it. Keep the read-directive hint (a niche signal
        # a skill can raise to demand activation).
        return [
            self.state_node("skill.read:hint", when="has_read_directive", order=2,
                            produce=self._produce_read_hint,
                            desc="ACTIVATING: a skill declares a read directive — call skill.read"),
        ]

    def _produce_read_hint(self, ctx: TurnContext) -> list[ContextNode]:
        out = []
        for s in active_skill_packs(ctx):
            target = getattr(s, "read_directive", None) or getattr(s, "read_target", None)
            if not target:
                continue
            sid = getattr(s, "id", None) or getattr(s, "name", None) or "skill"
            out.append(ContextNode(
                id=f"skill.read:hint:{sid}", kind="skill_read_hint",
                text=f"Skill '{sid}' is a strong candidate for '{target}': activate it with ActivateSkill before answering.",
                menu_hint=f"read directive for {sid}"))
        return out


LOBE = SkillSelectLobe()
SPEC = LOBE.spec  # back-compat export
