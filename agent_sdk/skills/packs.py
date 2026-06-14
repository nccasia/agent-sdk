"""Skill packs & registry — the skill logic layer.

``SkillPack`` is the runtime shape of a skill (the ported contract the engine
consumes); ``SkillRegistry`` is the per-turn view of the skills a bot has, with
DB rows overlaying builtin/plugin packs by slug. ``stage_matches`` /
``policy_skill_slugs`` / ``merge_extra_skill_slugs`` resolve which skills are
active for a turn/stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SkillPack:
    id: str  # slug — referenced from BotPolicy.capabilities.skills
    stages: tuple[str, ...]
    instructions: str
    required_tools: tuple[str, ...] = ()
    name: str = ""
    description: str = ""
    injection: str = "eager"  # eager | on_demand
    # Reference files (layer 3): bundle-relative path → markdown content.
    # The body is the MAP; detail lives here and is read file-by-file /
    # section-by-section, never whole-bundle (RFC 0013 layered skills).
    files: dict[str, str] = field(default_factory=dict)
    # Declarative onboarding/wizard checklist (ordered input steps + terminal
    # action). Materialized into an active task with one todo per step; the
    # task_execution lobe drives progressive input collection. Empty for
    # ordinary skills.
    checklist: tuple[dict, ...] = ()
    # Custom per-skill context variables (general workspace state a skill carries
    # while ACTIVE). Each: {key, type: checklist|todos|notes|var, title?, items?,
    # value?}. Surfaced as pinned context by the skill_active lobe and persisted
    # via the memory/scratchpad tools under `skill:<id>:<key>`. The legacy
    # ``checklist`` field is exposed as one ``type: checklist`` var (back-compat).
    context_vars: tuple[dict, ...] = ()
    # Folder this skill was loaded from (when loaded from a SKILL.md folder). Lets the
    # compiled-surface cache persist a SKILL.compiled.json sidecar next to it. None for
    # code/DB skills (cache stays in-process only).
    source_dir: str | None = None

    def all_context_vars(self) -> list[dict]:
        """The skill's context vars, including the legacy ``checklist`` as a
        ``type: checklist`` var so both surface uniformly."""
        out = [dict(v) for v in self.context_vars if isinstance(v, dict)]
        if self.checklist and not any(v.get("type") == "checklist" for v in out):
            out.insert(
                0,
                {
                    "key": "checklist",
                    "type": "checklist",
                    "title": "Checklist",
                    "items": list(self.checklist),
                },
            )
        return out


KB_LOOKUP_SKILL = SkillPack(
    id="kb_lookup",
    name="Information lookup",
    description="Look up facts in the bot's knowledge bases and answer with citations.",
    stages=("simple_answer", "research", "synthesize"),
    required_tools=("kb.retrieve", "kb.read_chunk"),
    instructions="""SKILL: KB lookup with citations
- Use KB retrieval tools for factual knowledge questions.
- Start broad with kb.retrieve or search tools, then read exact chunks when needed.
- Prefer a short grounded answer over a broad speculative answer.
- Every factual claim must be supported by source chunks.
- If the retrieved context does not answer the question, say you cannot confirm it from the knowledge base.""",
)


_BUILTIN_SKILLS = {
    KB_LOOKUP_SKILL.id: KB_LOOKUP_SKILL,
}


class SkillRegistry:
    """Per-turn view of the skills available to a bot.

    DB rows (loaded by the cli) override builtin fallbacks by slug.
    """

    def __init__(self, packs: list[SkillPack] | None = None):
        self._by_id: dict[str, SkillPack] = dict(_BUILTIN_SKILLS)
        for pack in packs or []:
            self._by_id[pack.id] = pack

    @classmethod
    def from_rows(cls, rows: list[dict] | None) -> SkillRegistry:
        packs: list[SkillPack] = []
        for row in rows or []:
            if not isinstance(row, dict) or not row.get("slug"):
                continue
            files = row.get("files") or {}
            if isinstance(files, str):  # tolerate str-JSON from raw SQL rows
                import json

                try:
                    files = json.loads(files)
                except json.JSONDecodeError:
                    files = {}
            checklist = row.get("checklist") or []
            if isinstance(checklist, str):  # tolerate str-JSON from raw SQL rows
                import json

                try:
                    checklist = json.loads(checklist)
                except json.JSONDecodeError:
                    checklist = []
            context_vars = row.get("context_vars") or []
            if isinstance(context_vars, str):  # tolerate str-JSON from raw SQL rows
                import json

                try:
                    context_vars = json.loads(context_vars)
                except json.JSONDecodeError:
                    context_vars = []
            packs.append(
                SkillPack(
                    id=str(row["slug"]),
                    name=str(row.get("name") or row["slug"]),
                    description=str(row.get("description") or ""),
                    stages=tuple(str(s) for s in (row.get("stages") or [])),
                    instructions=str(row.get("instructions") or ""),
                    required_tools=tuple(str(t) for t in (row.get("required_tools") or [])),
                    injection=str(row.get("injection") or "on_demand"),
                    files={str(k): str(v) for k, v in files.items()}
                    if isinstance(files, dict)
                    else {},
                    checklist=tuple(c for c in checklist if isinstance(c, dict))
                    if isinstance(checklist, list)
                    else (),
                    context_vars=tuple(v for v in context_vars if isinstance(v, dict))
                    if isinstance(context_vars, list)
                    else (),
                )
            )
        return cls(packs)

    def get(self, slug: str) -> SkillPack | None:
        return self._by_id.get(slug)

    def active_for_policy(self, policy: dict) -> list[SkillPack]:
        return [
            pack
            for slug in policy_skill_slugs(policy)
            if (pack := self._by_id.get(slug)) is not None
        ]

    def active_for_stage(self, policy: dict, stage_id: str) -> list[SkillPack]:
        return [
            pack for pack in self.active_for_policy(policy) if stage_matches(stage_id, pack.stages)
        ]


def stage_matches(stage_id: str, stages: tuple[str, ...]) -> bool:
    """Whether a production stage ``stage_id`` is one a skill (or override)
    targets. Production stage ids are flow-namespaced (``qna:synthesize``); a
    skill declares the LOGICAL step name (``synthesize``) so it activates on
    every flow's step of that name. An exact full-id match (``qna:synthesize``)
    still works for flow-specific targeting."""
    if stage_id in stages:
        return True
    suffix = stage_id.rsplit(":", 1)[-1]
    return suffix != stage_id and suffix in stages


def policy_skill_slugs(policy: dict) -> list[str]:
    """Slugs the policy selects — capabilities.skills, with legacy aliases.

    ``memory_enabled: false`` makes context_management inert here, the single
    seam every active_for_* path goes through — the skill's instructions,
    the memory tool, and the write cue all vanish together.
    """
    capabilities = policy.get("capabilities") or {}
    if not isinstance(capabilities, dict):
        return []
    slugs = (
        capabilities.get("skills")
        or capabilities.get("tool_packs")  # legacy
        or []
    )
    if not isinstance(slugs, list):
        return []
    out = [str(s) for s in slugs]
    if not policy.get("memory_enabled", True):
        out = [s for s in out if s != "context_management"]
    return out


def merge_extra_skill_slugs(policy: dict, extra: list) -> dict:
    """Union per-turn skill slugs (e.g. a fired task's template skills) into a
    COPY of the policy's capabilities.skills — never mutates the input (the
    policy dict may be shared/cached by the caller). Order-preserving dedupe;
    no-op (same object back) when ``extra`` is empty."""
    extra_slugs = [str(s) for s in (extra or []) if str(s).strip()]
    if not extra_slugs:
        return policy
    merged = list(dict.fromkeys([*policy_skill_slugs(policy), *extra_slugs]))
    capabilities = policy.get("capabilities")
    capabilities = dict(capabilities) if isinstance(capabilities, dict) else {}
    capabilities["skills"] = merged
    return {**policy, "capabilities": capabilities}
