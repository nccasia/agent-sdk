"""skill_active — the SELECTED skill states: drive the active skill.

WHAT   Once a skill is loaded, mark it in use and drive its workflow — execute
       the skill's remaining steps until the task is done.
WHEN   Fires when at least one skill is in use this turn (`skills_in_use`).
HOW    context → state → activation → prompt. :meth:`state` resolves the selected
       lifecycle (activated → driving); each state_node emits its piece. The
       NON-SELECTED states (listing/selecting) are the sibling `skill_select`
       lobe; the two coexist (drive the loaded one while the rest stay listed).

         [skill_select: listing ─▶ selecting ─▶ activating] ─▶ activated ─▶ driving
                                                                in_use       guide
"""

from __future__ import annotations

from agent_sdk.lobes.runtime import Lobe, TurnContext
from agent_sdk.network.activation import LAYER_SKILL, LobeNode
from agent_sdk.network.context_builder import ContextNode
from agent_sdk.skills.lobes._common import active_skill_packs, skill_flags


class SkillActiveLobe(Lobe):
    """The active-skill lobe (selected states) — inject + drive the loaded skill."""

    id = "skill_active"
    name = "Skill Active"
    description = "Inject the active skill and drive its workflow to completion."
    use_when = "a skill is loaded and the turn is being driven through it"
    how = (
        "Resolve the selected state and emit its piece: mark the skill in use, then "
        "drive the loaded skill's content — execute its remaining steps in order, "
        "reading referenced files/sections only as each step needs them, until done."
    )
    system_prompt = None  # control lobe — emits markers/guide, not one template
    behavior = "select"
    layer = LAYER_SKILL
    order = 1
    writes = ("skill_pack",)

    def state(self, ctx: dict) -> str:
        if not ctx.get("skills_in_use"):
            return ""
        return "driving"

    def activation(self, ctx: dict) -> float:
        return 1.0 if ctx.get("skills_in_use") else 0.0

    def _signal_ctx_for(self, ctx: TurnContext) -> dict:
        base = super()._signal_ctx_for(ctx)
        base.update(skill_flags(ctx))
        return base

    def state_machine(self) -> list[LobeNode]:
        return [
            self.state_node(
                "skill:in_use",
                when="skills_in_use",
                order=0,
                produce=self._produce_in_use,
                desc="ACTIVATED: the 'N skills in use' marker",
            ),
            self.state_node(
                "skill:guide",
                when="skills_in_use",
                order=1,
                stability="volatile",
                produce=self._produce_guide,
                desc="DRIVING: a skill is loaded — execute remaining steps until done",
            ),
            self.state_node(
                "skill:context_vars",
                when="skills_in_use",
                order=2,
                stability="volatile",
                produce=self._produce_context_vars,
                desc="DRIVING: the active skill's context vars (checklist/todos/notes)",
            ),
        ]

    def _in_use_packs(self, ctx: TurnContext) -> list:
        """The skill packs currently in use (driving). ``skills_in_use`` is a list
        of slugs (set by the interpreter at the skill.read moment)."""
        in_use = ctx.lobe_outputs.get("skills_in_use") or []
        slugs = set(in_use) if isinstance(in_use, (list, tuple, set)) else set()
        if not slugs:
            return []
        out = [p for p in active_skill_packs(ctx) if str(getattr(p, "id", "")) in slugs]
        reg = ctx.lobe_outputs.get("skill_registry")
        if reg is not None and hasattr(reg, "get"):
            have = {str(getattr(p, "id", "")) for p in out}
            for s in slugs - have:
                p = reg.get(s)
                if p is not None:
                    out.append(p)
        return out

    def _produce_context_vars(self, ctx: TurnContext) -> list[ContextNode]:
        from agent_sdk.skills import render_context_var

        out: list[ContextNode] = []
        for pack in self._in_use_packs(ctx):
            sid = str(getattr(pack, "id", "skill"))
            getvars = getattr(pack, "all_context_vars", None)
            for var in getvars() if callable(getvars) else []:
                if not isinstance(var, dict):
                    continue
                key = str(var.get("key") or "var")
                out.append(
                    ContextNode(
                        id=f"skill:context:{sid}:{key}",
                        kind="skill_context",
                        text=render_context_var(sid, var),
                        pinned=True,
                        menu_hint=f"{sid} {key}",
                    )
                )
        return out

    def _produce_in_use(self, ctx: TurnContext) -> list[ContextNode]:
        if not ctx.lobe_outputs.get("skills_in_use"):
            return []
        n = len(active_skill_packs(ctx))
        return [
            ContextNode(
                id="skill:in_use:marker",
                kind="skill_marker",
                text=f"You have {n} skill{'s' if n != 1 else ''} in use.",
                menu_hint=f"{n} skills in use",
            )
        ]

    def _produce_guide(self, ctx: TurnContext) -> list[ContextNode]:
        in_use = ctx.lobe_outputs.get("skills_in_use")
        if not in_use:
            return []
        names = ", ".join(str(s) for s in in_use) if isinstance(in_use, (list, tuple)) else ""
        label = f" ({names})" if names else ""
        # Pinned: when a skill is driving, this is the turn's primary instruction —
        # it must not be tiered out. Tight: only what the model needs to keep going.
        return [
            ContextNode(
                id="skill:guide",
                kind="skill_guide",
                pinned=True,
                text=(
                    f"Skill in use{label}: follow its steps in order to completion. Read a "
                    "reference section (skill.read file+section, or skill.search) only when a "
                    "step needs it — not whole files. Finish all steps before answering."
                ),
                menu_hint="skill workflow in progress",
            )
        ]


LOBE = SkillActiveLobe()
SPEC = LOBE.spec  # back-compat export
