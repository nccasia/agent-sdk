"""Skill packs — instruction packs with progressive disclosure (RFC 0013).

Skills live in the DB skill registry (``skills`` table, ``/v1/skills`` API);
the cli loads the rows selected by the bot's policy and passes them to the
interpreter via ``settings["skills"]``. The hardcoded ``KB_LOOKUP_SKILL``
remains as an offline fallback (eval/standalone paths with no DB) and keeps
its original eager behavior.

Progressive disclosure: ``injection == "eager"`` inlines the full instructions
into the stage prompt; ``injection == "on_demand"`` exposes only
``name: description`` plus a directive to call the ``skill.read`` tool, so the
model loads a skill's body only when its reasoning decides it's relevant.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


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


def render_context_var(skill_id: str, var: dict) -> str:
    """Render one context var as the authoritative live workspace block.

    Shared by the ``skill_active`` lobe (eager / next-turn surfacing) and the
    ``skill.read`` tool result (on_demand commitment moment), so the model sees
    the SAME live state wherever it lands. ``checklist``/``todos`` render as a
    numbered status list; other types as a ``title: value`` line."""
    key = str(var.get("key") or var.get("title") or "var")
    title = str(var.get("title") or key)
    vtype = str(var.get("type") or "var")
    if vtype in ("checklist", "todos"):
        lines = [f"### Skill {skill_id} · {title}"]
        for i, it in enumerate(var.get("items") or []):
            if isinstance(it, dict):
                label = it.get("title") or it.get("ask") or it.get("key") or "item"
                status = it.get("status") or "todo"
            else:
                label, status = str(it), "todo"
            lines.append(f"  {i + 1}. [{status}] {label}")
        lines.append(
            f"Advance the next open item, then persist progress under "
            f"`skill:{skill_id}:{key}` via todos.update / memory."
        )
        return "\n".join(lines)
    val = var.get("value")
    body = (
        f"{title}: {val}"
        if val
        else (f"{title} (empty) — track it under `skill:{skill_id}:{key}` via the memory tool")
    )
    return f"### Skill {skill_id} · {body}"


def render_context_vars_block(pack: Any) -> str:
    """The full pinned context-vars block for a skill pack, or ``""`` if it
    declares none. One ``render_context_var`` per var under a short header."""
    getvars = getattr(pack, "all_context_vars", None)
    vars_ = getvars() if callable(getvars) else []
    rendered = [
        render_context_var(str(getattr(pack, "id", "skill")), v)
        for v in vars_
        if isinstance(v, dict)
    ]
    if not rendered:
        return ""
    return (
        "Live workspace state for this skill (authoritative — recomputed "
        "every turn):\n" + "\n".join(rendered)
    )


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

# Deliberately pushy — measured by benchmarks/skillbench: with a soft
# "before relying on…" phrasing the model routinely skipped activation and
# free-styled the task (activation recall 0 on the code_review fixture).
_ON_DEMAND_DIRECTIVE = (
    "IMPORTANT: when the user's request matches one of these skills, you MUST "
    "first call the `ActivateSkill` tool with that skill's slug to load its "
    "full instructions, and then follow them. Activating a skill is a "
    "deliberate choice — pick the one that fits; do not attempt the task from "
    "the one-line summary alone."
)


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
        return [pack for pack in self.active_for_policy(policy) if stage_id in pack.stages]


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
) -> str:
    """The per-stage skill section of the system prompt.

    Eager skills inline their full instructions (pre-RFC behavior); on-demand
    skills contribute only a one-line index entry plus the skill.read directive.

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
    eager: list[str] = []
    on_demand: list = []  # (label, desc) in registry order
    for pack in packs:
        if pack.injection == "on_demand":
            on_demand.append((pack.name or pack.id, pack.description or "(no description)"))
        else:
            eager.append(pack.instructions)

    adaptive = str(policy.get("skill_strategy") or "static") == "adaptive"
    if adaptive and query and on_demand:
        on_demand = _rank_on_demand_skills(on_demand, query, q_vec, embed_one, policy, ranking_out)
    elif ranking_out is not None:
        for label, desc in on_demand:
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
    for rank, (idx, label, desc, sc) in enumerate(by_rank):
        if sc["activation"] >= floor or rank < _SKILL_MIN_KEEP:
            keep_idx.add(idx)
    if ranking_out is not None:
        for idx, label, desc, sc in scored:
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


# ── layered reading: sections, ToC, search (RFC 0013 layered skills) ─────────
#
# Deterministic markdown navigation so the model reads large bundles
# progressively — index → ToC → one section — instead of dumping files.

# A file at or below this estimated size is returned whole; above it, a bare
# file read returns the ToC and the model requests a section.
FULL_FILE_TOKENS = 1500


@dataclass(frozen=True)
class Section:
    id: str
    heading: str
    content: str
    line_count: int


def est_tokens(text: str) -> int:
    return max(0, len(text or "") // 4)


_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)


def _slugify_heading(heading: str) -> str:
    norm = unicodedata.normalize("NFKD", heading)
    ascii_text = norm.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug or "section"


def split_sections(markdown: str) -> list[Section]:
    """Split markdown by #/##/### headings. Content before the first heading
    becomes a synthetic "intro" section. Section ids are slugified headings,
    deduped with -2, -3 … suffixes."""
    text = markdown or ""
    matches = list(_HEADING_RE.finditer(text))
    sections: list[Section] = []
    seen: dict[str, int] = {}

    def add(heading: str, content: str) -> None:
        base = _slugify_heading(heading)
        seen[base] = seen.get(base, 0) + 1
        sid = base if seen[base] == 1 else f"{base}-{seen[base]}"
        sections.append(
            Section(
                id=sid,
                heading=heading,
                content=content.strip("\n"),
                line_count=content.count("\n") + 1,
            )
        )

    if not matches:
        if text.strip():
            add("intro", text)
        return sections

    if text[: matches[0].start()].strip():
        add("intro", text[: matches[0].start()])
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        add(m.group(2), text[m.start() : end])
    return sections


def file_toc(content: str) -> str:
    """A table of contents for a large file: section ids, headings, sizes."""
    lines = ["Table of contents (request one section at a time):"]
    for sec in split_sections(content):
        lines.append(f"- [{sec.id}] {sec.heading} (~{est_tokens(sec.content)} tokens)")
    return "\n".join(lines)


def file_purpose(content: str) -> str:
    """One-line purpose for the layer-2 file index: frontmatter description,
    else the first heading, else the first non-empty line."""
    text = (content or "").lstrip()
    if text.startswith("---"):
        for line in text.splitlines()[1:30]:
            if line.strip() == "---":
                break
            if line.lower().startswith("description:"):
                return line.split(":", 1)[1].strip()
    m = _HEADING_RE.search(text)
    if m:
        return m.group(2)
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:120]
    return "(empty)"


def _nfc_lower(s: str) -> str:
    return unicodedata.normalize("NFC", s or "").lower()


def search_bundle(packs: list[SkillPack], query: str, top_k: int = 5) -> list[dict]:
    """Keyword search over every section of every file in the given skills.
    Deterministic token-overlap scoring (NFC-normalized for Vietnamese) — the
    fast path through very large bundles. Returns
    {skill, file, section, heading, score, snippet} hits."""
    terms = [t for t in re.split(r"\W+", _nfc_lower(query)) if len(t) > 1]
    if not terms:
        return []
    hits: list[dict] = []
    for pack in packs:
        sources = {"SKILL.md": pack.instructions, **pack.files}
        for path, content in sources.items():
            for sec in split_sections(content):
                hay = _nfc_lower(sec.heading + "\n" + sec.content)
                score = sum(hay.count(t) for t in terms)
                if score <= 0:
                    continue
                # Snippet: first line containing a term, trimmed.
                snippet = ""
                for line in sec.content.splitlines():
                    if any(t in _nfc_lower(line) for t in terms):
                        snippet = line.strip()[:200]
                        break
                hits.append(
                    {
                        "skill": pack.name or pack.id,
                        "file": path,
                        "section": sec.id,
                        "heading": sec.heading,
                        "score": score,
                        "snippet": snippet,
                    }
                )
    hits.sort(key=lambda h: (-h["score"], h["file"], h["section"]))
    return hits[:top_k]
