"""Skill prompt building — how a skill's information enters the reasoning context.

``build_skill_prompt_block`` renders the per-stage skill section: eager skills
inline their full instructions; on-demand skills contribute a one-line index entry
plus the (deliberately pushy) ``ActivateSkill`` directive. With
``skill_strategy == "adaptive"`` the on-demand index is ranked by relevance and
trimmed. This is the OY-context side of skills; the runtime tools (``runtime.py``)
are the OX side that loads the body the model then follows.
"""

from __future__ import annotations

from agent_sdk.skills.packs import SkillRegistry

# Deliberately pushy AND lifecycle-teaching — measured by benchmarks/skillbench: a
# soft phrasing makes the model skip activation (recall 0 on code_review); and
# without the "search → section" guidance it blind-activates then reads whole
# files (75% no-search / 80% full-read in the run). One directive, both lessons.
_ON_DEMAND_DIRECTIVE = (
    "IMPORTANT: if a skill matches the request, you MUST call `ActivateSkill(slug)` "
    "once to load it, then follow its steps — do not work from the summary. If it "
    "has reference files, use `skill.search(query)` to find the relevant section, "
    "then `skill.read(file, section=…)` — never read whole files."
)

# High-recall floor: never trim below this many top-ranked on_demand skills
# even when all score under the activation floor — the model still needs to
# DISCOVER a skill before it can `skill.read` it.
_SKILL_MIN_KEEP = 3


def build_skill_prompt_block(
    registry: SkillRegistry,
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
    """The per-stage skill section of the system prompt.

    Eager skills inline their full instructions (pre-RFC behavior); on-demand
    skills contribute only a one-line index entry plus the skill.read directive.

    State-aware (RFC 0013 lifecycle): an on-demand skill already in use
    (``skills_in_use``) is dropped from the index — the ``skill_active`` lobe
    drives it, so re-listing it + the "go activate one" directive every hop is
    wasted tokens. If that leaves no on-demand skills to offer, the index +
    directive are omitted entirely.

    Adaptive skill list (``policy.skill_strategy == "adaptive"``, opt-in): when a
    turn ``query`` is supplied the on_demand index is RANKED by relevance to the
    query (the shared ``score_relevance`` scorer) and trimmed to the relevant
    ones — high-recall (keeps everything ≥ ``skill_min_activation``, and always
    at least the top ``_SKILL_MIN_KEEP``). Eager skills are NEVER trimmed; a
    trimmed skill stays registered and ``skill.read``-able by slug. Default
    (``static``) is the legacy full list, byte-identical. ``ranking_out``, if
    given, is filled with one ``{label, l1, l2, activation, kept}`` row per
    on_demand skill for the inspector.
    """
    packs = registry.active_for_stage(policy, stage_id)
    # LLM-reasoned activation (skill_strategy="reason"): scope to the DECLARED
    # skills the reasoning step chose (written to the turn context, passed here
    # as active_slugs). Narrowing only — never adds an undeclared skill. Empty/no
    # match ⇒ fall back to all-declared (a flaky reasoner never zeroes the bot).
    if active_slugs is not None:
        keep = {str(s) for s in active_slugs}
        scoped = [p for p in packs if p.id in keep or (p.name or "") in keep]
        if scoped:
            packs = scoped
    in_use = {str(s) for s in (skills_in_use or [])}
    eager: list[str] = []
    on_demand: list = []  # (label, desc) in registry order
    for pack in packs:
        # A skill already in use is driven by skill_active — don't re-list it or
        # re-issue the "activate one" directive (state-aware, saves tokens/hop).
        if pack.id in in_use or (pack.name or "") in in_use:
            continue
        if pack.injection == "on_demand":
            on_demand.append((pack.name or pack.id, pack.description or "(no description)"))
        else:
            eager.append(pack.instructions)

    adaptive = str(policy.get("skill_strategy") or "static") == "adaptive"
    if adaptive and query and on_demand:
        on_demand = _rank_on_demand_skills(on_demand, query, q_vec, embed_one, policy, ranking_out)
    elif ranking_out is not None:
        for label, _desc in on_demand:
            ranking_out.append({"label": label, "kept": True})

    index = [f"- {label}: {desc}" for label, desc in on_demand]
    parts = list(eager)
    if index:
        parts.append("Available skills:\n" + "\n".join(index) + "\n" + _ON_DEMAND_DIRECTIVE)
    return "\n\n".join(p for p in parts if p)


def _rank_on_demand_skills(on_demand, query, q_vec, embed_one, policy, ranking_out):
    """Rank (label, desc) on_demand entries by relevance; return the kept ones
    in original registry order. High-recall: keep ≥ floor, always ≥ top-K."""
    from agent_sdk.network.context_builder import merge_weights, score_relevance

    floor = float(policy.get("skill_min_activation", 0.2) or 0.2)
    weights = merge_weights(policy.get("skill_weights"))
    scored = []  # (idx, label, desc, sc)
    for idx, (label, desc) in enumerate(on_demand):
        sc = score_relevance(query, q_vec, f"{label} {desc}", embed_one=embed_one, weights=weights)
        scored.append((idx, label, desc, sc))
    by_rank = sorted(scored, key=lambda x: -x[3]["activation"])
    keep_idx = set()
    for rank, (idx, _label, _desc, sc) in enumerate(by_rank):
        if sc["activation"] >= floor or rank < _SKILL_MIN_KEEP:
            keep_idx.add(idx)
    if ranking_out is not None:
        for idx, label, _desc, sc in scored:
            ranking_out.append(
                {
                    "label": label,
                    "l1": round(sc["l1"], 3),
                    "l2": round(sc["l2"], 3),
                    "activation": round(sc["activation"], 3),
                    "kept": idx in keep_idx,
                }
            )
    return [(label, desc) for idx, (label, desc) in enumerate(on_demand) if idx in keep_idx]


def resolve_skill_instructions(policy: dict, stage_id: str) -> str:
    """Builtin-only resolution — kept for callers without a DB-backed registry."""
    return build_skill_prompt_block(SkillRegistry(), policy, stage_id)
