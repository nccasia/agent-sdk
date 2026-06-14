"""Scoring for skillbench — the deterministic checks behind each behavior group.

Pure helpers, no I/O and no provider: ``run.py`` drives the live agent and feeds
the probe traces in here. Five groups, each a list of ``{id, ok, detail, skill}``
check rows:

- ``parse``    — the SOP folder parses into a usable structure (sections, ToC, search).
- ``mapping``  — the skill maps onto the engine: block only in declared stages,
                 on_demand→index+ActivateSkill / eager→inline, activation tool exposed.
- ``activation`` — the model loads the right skill, not the distractors (precision/recall).
- ``follow``   — the answer obeys the loaded skill's mandated behavior.
- ``funnel``   — skill content navigates (search/section reads), it does not dump.

A row's ``skill`` lets ``run.py`` roll the verdict up per skill.
"""

from __future__ import annotations

import re
from typing import Any

from agent_sdk.skill_runtime import ACTIVATE
from agent_sdk.skills import (
    FULL_FILE_TOKENS,
    SkillRegistry,
    build_skill_prompt_block,
    est_tokens,
    search_bundle,
    split_sections,
)

# ── thresholds (the readiness bar) ───────────────────────────────────────────
ACTIVATION_RECALL_MIN = 0.8
ACTIVATION_PRECISION_MIN = 0.8
FOLLOW_GAIN_MIN = 0.0
DISCLOSURE_RATIO_MAX = 0.35  # bundle skills: skill-tool tokens ÷ bundle tokens
FUNNEL_PEAK_CHARS_MAX = 12_000

_VAGUE = ("help you with stuff", "do the thing", "i can help", "various things", "anything")


def row(cid: str, ok: bool, detail: str = "", skill: str = "", diag: bool = False) -> dict:
    """A check row. ``diag=True`` marks it diagnostic — surfaced in the report but
    NOT gating (it never fails a group or a skill verdict)."""
    return {"id": cid, "ok": bool(ok), "detail": str(detail), "skill": skill, "diag": diag}


def lint_checks(negatives: list) -> list[dict]:
    """Adversarial fixtures (slug starts with ``_``) must be REJECTED by the deterministic
    gates: the bench passes when a deliberately-bad skill is flagged by SOME validator —
    a vague description (``_bad_vague``), a large non-navigable reference file
    (``_bad_headings``), or a degenerate checklist (``_bad_checklist``). A fixture caught
    by any one defect counts as rejected; one that slips through every gate is the failure."""
    out: list[dict] = []
    for sk in negatives:
        issues = negative_defects(sk)
        out.append(row(f"lint.rejects[{sk.id}]", bool(issues),
                       f"flagged: {'; '.join(issues) or 'NOT flagged (bad!)'}"))
    return out


# ── parse ─────────────────────────────────────────────────────────────────────
def description_issues(description: str) -> list[str]:
    """Lint a skill description (the index entry the model picks from)."""
    d = (description or "").strip()
    issues: list[str] = []
    if len(d) < 30:
        issues.append("description too short (needs WHAT + WHEN)")
    if len(d) > 1024:
        issues.append("description > 1024 chars")
    low = d.lower()
    if any(v in low for v in _VAGUE):
        issues.append("vague description (no concrete trigger)")
    if "use when" not in low and "use this" not in low and "когда" not in low \
            and not any(w in low for w in ("when ", "khi ", "if the user", "for ")):
        issues.append("no WHEN signal (say when to use it)")
    return issues


def large_files(skill: Any) -> dict[str, str]:
    """The reference files big enough to require layered reading (a ToC, not a dump)."""
    return {f: c for f, c in (skill.files or {}).items() if est_tokens(c) > FULL_FILE_TOKENS}


def toc_navigable(content: str) -> bool:
    """A large file is navigable when it splits into ≥2 real sections (so a ToC can
    anchor on a named heading). Counted from ``split_sections`` — the bullet-count
    heuristic over-counted by one and could never flag a single-section dump."""
    return len(split_sections(content)) >= 2


def checklist_valid(skill: Any) -> bool:
    """A checklist skill materializes ordered steps, each with something to present
    (a ``title`` or an ``ask``). Skills with no checklist are vacuously valid."""
    steps = list(getattr(skill, "checklist", None) or [])
    if not steps:
        return True
    return all(isinstance(s, dict) and (s.get("title") or s.get("ask")) for s in steps)


def parse_checks(skill: Any) -> list[dict]:
    """Deterministic per-skill structure checks over a loaded Skill."""
    sid = skill.id
    out: list[dict] = []
    issues = description_issues(skill.description)
    out.append(row(f"parse.{sid}.description", not issues, "; ".join(issues) or "ok", sid))

    body = skill.instructions or ""
    secs = split_sections(body)
    out.append(row(f"parse.{sid}.body", bool(body.strip()) and bool(secs),
                   f"{len(secs)} section(s)", sid))

    # Layered reading: a large reference file must yield a navigable ToC, not a dump.
    for fname, content in large_files(skill).items():
        n = len(split_sections(content))
        out.append(row(f"parse.{sid}.toc[{fname}]", toc_navigable(content),
                       f"large file → ToC of {n} section(s)", sid))

    # A checklist skill must materialize ordered steps.
    if getattr(skill, "checklist", None):
        steps = list(skill.checklist)
        out.append(row(f"parse.{sid}.checklist", checklist_valid(skill),
                       f"{len(steps)} step(s)", sid))
    return out


def negative_defects(skill: Any) -> list[str]:
    """Every deterministic defect an adversarial fixture trips — the union of the
    description lint and the structural validators (ToC navigability, checklist).
    Used by ``lint_checks`` to reject a bad skill caught by ANY gate."""
    defects = list(description_issues(skill.description))
    for fname, content in large_files(skill).items():
        if not toc_navigable(content):
            defects.append(f"{fname}: large file not navigable (no ToC)")
    if getattr(skill, "checklist", None) and not checklist_valid(skill):
        defects.append("degenerate checklist (step missing title/ask)")
    return defects


def search_self_locates(skills: list[Any], term: str, want_skill: str, want_file: str) -> dict:
    """search_bundle finds ``term`` in the expected skill/file (the rich-index check)."""
    packs = [s.to_pack() for s in skills]
    hits = search_bundle(packs, term, top_k=3)
    ok = any(h["file"] == want_file for h in hits)
    detail = ", ".join(f"{h['file']}#{h['section']}" for h in hits) or "(no hits)"
    return row(f"parse.{want_skill}.search", ok, f"{term!r} → {detail}", want_skill)


# ── mapping (skill → stages, lobes, tools) ───────────────────────────────────
def mapping_checks(skills: list[Any], exposed_tool_names: set[str]) -> list[dict]:
    """Deterministic stage/lobe/tool mapping over the rendered skill prompt block."""
    registry = SkillRegistry([s.to_pack() for s in skills])
    policy = {"capabilities": {"skills": [s.id for s in skills]}, "skill_strategy": "static"}
    out: list[dict] = []
    has_on_demand = any(s.disclosure == "on_demand" for s in skills)

    for sk in skills:
        sid = sk.id
        declared = sk.stages[0] if sk.stages else "synthesize"
        block_in = build_skill_prompt_block(registry, policy, declared)
        # a stage the skill does NOT declare (use one it almost certainly lacks)
        off = "cite" if "cite" not in sk.stages else "filter"
        block_off = build_skill_prompt_block(registry, policy, off)

        in_declared = (sk.name in block_in) or (sk.description[:40] in block_in) \
            or (sk.instructions[:40] in block_in)
        out.append(row(f"mapping.{sid}.in_declared_stage", in_declared,
                       f"present in {declared!r}", sid))
        # eager bodies / on_demand index should not leak into a non-declared stage
        leaked = (sk.name in block_off) or (sk.instructions[:40] in block_off)
        out.append(row(f"mapping.{sid}.absent_off_stage", not leaked,
                       f"absent from {off!r}", sid))

        if sk.disclosure == "on_demand":
            out.append(row(f"mapping.{sid}.index_and_directive",
                           (sk.name in block_in) and (ACTIVATE in block_in),
                           "one-line index + ActivateSkill directive", sid))
        else:  # eager
            out.append(row(f"mapping.{sid}.inlined",
                           sk.instructions[:40] in block_in,
                           "body inlined (no activation needed)", sid))

    if has_on_demand:
        out.append(row("mapping.activation_tool_exposed", ACTIVATE in exposed_tool_names,
                       f"ActivateSkill in exposed tools: {ACTIVATE in exposed_tool_names}"))
    return out


# ── activation (did the model load the right skill?) ─────────────────────────
def activated_slugs(rec: Any) -> set[str]:
    """The skills the model ACTIVATED this turn (structural: ActivateSkill calls)."""
    return {
        str((c.get("input") or {}).get("slug"))
        for c in getattr(rec, "tool_calls", [])
        if c.get("name") == ACTIVATE and (c.get("input") or {}).get("slug")
    }


def prf(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(precision, 3),
            "recall": round(recall, 3), "f1": round(f1, 3)}


def overreach_metrics(overreach: int, scenarios: int, activated_total: int) -> dict:
    """Over-activation diagnostics: how often the model activated MORE on-demand skills
    than a scenario warranted (right skill + extra), and the mean it activates per
    scenario. Diagnostic — precision already penalizes activating a *wrong* skill; this
    isolates the runtime's lack of a single-skill-per-turn guard (ActivateSkill is plural)."""
    return {
        "activation.overreach_scenarios": f"{overreach}/{scenarios}" if scenarios else "0/0",
        "activation.avg_skills_activated": (round(activated_total / scenarios, 2)
                                            if scenarios else 0.0),
    }


def activation_checks(per_skill_counts: dict[str, dict]) -> tuple[list[dict], dict]:
    """Roll TP/FP/FN per skill into precision/recall checks + a metrics block."""
    out: list[dict] = []
    metrics: dict = {}
    for sid, c in sorted(per_skill_counts.items()):
        m = prf(c["tp"], c["fp"], c["fn"])
        metrics[f"{sid}.recall"] = m["recall"]
        metrics[f"{sid}.precision"] = m["precision"]
        ok = m["recall"] >= ACTIVATION_RECALL_MIN and m["precision"] >= ACTIVATION_PRECISION_MIN
        out.append(row(f"activation.{sid}", ok,
                       f"P={m['precision']} R={m['recall']} (tp{m['tp']} fp{m['fp']} fn{m['fn']})", sid))
    return out, metrics


# ── follow (did the answer obey the loaded skill?) ───────────────────────────
# A provider Message is a list of content blocks; on MiniMax a *thinking* block can
# leak into the captured answer as its ``str(ThinkingBlock(...))`` repr (the SDK's
# `_text_of` mis-reads a block whose .text is the repr). Strip those reprs so `follow`
# scores the model's REAL prose — and when nothing real remains, the answer was never
# surfaced (the turn ended thinking-only / hop-budget-exhausted), which `follow` should
# flag, not silently miss.
_BLOCK_REPR = re.compile(r"\b\w+Block\(.*?type='[a-z_]+'\)", re.DOTALL)


def clean_answer(text: str) -> str:
    """The model's real answer prose — block-object reprs stripped out."""
    return _BLOCK_REPR.sub("", text or "").strip()


def follow_checks(scenario: dict, rec: Any) -> list[dict]:
    up = scenario.get("uplift") or {}
    sid = up.get("skill") or (scenario.get("skills_under_test") or [""])[0]
    raw = (getattr(rec, "answer", "") or "")
    ans = clean_answer(raw)
    out: list[dict] = []
    # Diagnostic: was a readable answer actually surfaced? An empty `ans` after cleaning
    # means the turn ended without prose (thinking-only / forced-final emitted a recovered
    # tool_use / hop budget exhausted) — the real reason a `follow` substring then fails.
    if not ans:
        out.append(row(f"follow.{scenario['id']}.answer_observable", False,
                       "no readable answer surfaced (thinking-only / no final text)",
                       sid, diag=True))
    for must in up.get("must_include", []):
        out.append(row(f"follow.{scenario['id']}.has[{must[:24]}]", must in ans,
                       f"includes {must!r}", sid))
    for nope in up.get("must_not_include", []):
        out.append(row(f"follow.{scenario['id']}.not[{nope[:24]}]", nope not in ans,
                       f"excludes {nope!r}", sid))
    # must_include_any: at least one of the alternatives appears (robust for
    # free-text behavior like "leads with a mitigation action", whose exact
    # wording varies — roll back / fail over / drain / mitigate / revert).
    anyset = up.get("must_include_any") or []
    if anyset:
        low = ans.lower()
        hit = next((t for t in anyset if t.lower() in low), None)
        out.append(row(f"follow.{scenario['id']}.any", hit is not None,
                       f"any of {anyset} → {hit!r}", sid))
    return out


# ── funnel (did skill content navigate, not dump?) ───────────────────────────
def funnel_peak_chars(rec: Any) -> int:
    series = [c for s in getattr(rec, "stages", [])
              for c in (s.get("metadata") or {}).get("funnel_obs_chars", [])]
    return max(series or [0])


def disclosure_ratio(rec: Any, bundle_tokens: int) -> float:
    """Skill-tool output tokens ÷ total bundle tokens — how much of the SOP the model
    pulled into context. Low = it navigated (search/section); high = it dumped."""
    if not bundle_tokens:
        return 0.0
    pulled = sum(
        est_tokens(str(c.get("output") or ""))
        for c in getattr(rec, "tool_calls", [])
        if str(c.get("name", "")).startswith("skill") or c.get("name") == ACTIVATE
    )
    return round(pulled / bundle_tokens, 3)


def navigated(rec: Any) -> bool:
    """Did the model navigate a bundle — use skill.read / skill.search — rather
    than swallow it via one big ActivateSkill? Deterministic and robust (binary),
    unlike the token-ratio which hovers at its threshold across runs."""
    return any(
        c.get("name") in ("skill.read", "skill.search")
        for c in getattr(rec, "tool_calls", [])
    )


def funnel_checks(scenario: dict, rec: Any, bundle_tokens: int) -> list[dict]:
    sid = (scenario.get("skills_under_test") or [""])[0]
    out: list[dict] = []
    peak = funnel_peak_chars(rec)
    if peak:
        out.append(row(f"funnel.{scenario['id']}.bounded", peak < FUNNEL_PEAK_CHARS_MAX,
                       f"obs tail peak {peak} chars", sid))
    if bundle_tokens > FULL_FILE_TOKENS:
        # Gate on navigation (robust); report the disclosure ratio as a diagnostic
        # (it sits right at its threshold and flips on model variance).
        out.append(row(f"funnel.{scenario['id']}.navigated", navigated(rec),
                       "used skill.read/skill.search (layered, not a dump)", sid))
        dr = disclosure_ratio(rec, bundle_tokens)
        out.append(row(f"funnel.{scenario['id']}.disclosure", dr <= DISCLOSURE_RATIO_MAX,
                       f"disclosure ratio {dr} (≤{DISCLOSURE_RATIO_MAX}; diagnostic)",
                       sid, diag=True))
    return out


# ── lifecycle efficiency (run-level diagnostic metrics) ──────────────────────
def lifecycle_metrics(probes: list[Any]) -> dict:
    """How efficiently the run drove the skill lifecycle (no-select → search →
    activate → read). Diagnostic, not gating — surfaced in the report so the
    before/after token win is visible. Reads the probe tool_calls + llm_calls."""
    import re as _re
    turns = len(probes) or 1
    in_tok = 0
    activ_turns = search_turns = 0
    reads = sections = redundant = 0
    surf_tok: list[int] = []          # ActivateSkill result size (surface compactness)
    refs_total = refs_read = 0        # chunk refs in the surface vs chunks actually read
    read_misses = recovered = 0       # skill.read/search that errored, and whether the
    #                                   model recovered (a later successful read/search)
    for p in probes:
        # Missing-section recovery: a skill.read for a section that does not exist (or a
        # search with no matches) returns an "Error:"/"(no matches" string. A robust model
        # re-navigates afterwards; flag the turn when it gives up instead.
        tcs_seq = getattr(p, "tool_calls", [])
        skill_tc = [c for c in tcs_seq if str(c.get("name", "")).startswith("skill")]
        for i, c in enumerate(skill_tc):
            out_s = str(c.get("output") or "")
            if out_s.startswith("Error:") or out_s.startswith("(no matches"):
                read_misses += 1
                later = skill_tc[i + 1:]
                if any(not (str(d.get("output") or "").startswith("Error:")
                            or str(d.get("output") or "").startswith("(no matches"))
                       for d in later):
                    recovered += 1
    for p in probes:
        for c in getattr(p, "llm_calls", []):
            in_tok += int((c.get("usage") or {}).get("input_tokens", 0) or 0)
        tcs = getattr(p, "tool_calls", [])
        names = [c.get("name") for c in tcs]
        if ACTIVATE in names:
            activ_turns += 1
            if "skill.search" in names:
                search_turns += 1  # activation turn that also searched (search-first)
        # surface size + referenced-chunk read-back (per probe)
        read_ids = {f"{(c.get('input') or {}).get('file', '')}#{(c.get('input') or {}).get('section', '')}"
                    for c in tcs if c.get("name") == "skill.read"}
        read_ids |= {str((c.get("input") or {}).get("chunk"))
                     for c in tcs if c.get("name") == "skill.read" and (c.get("input") or {}).get("chunk")}
        for c in tcs:
            if c.get("name") == ACTIVATE:
                out = str(c.get("output") or "")
                surf_tok.append(est_tokens(out))
                for cid in _re.findall(r"\[([^\]\s]+#[^\]\s]+)\]", out):
                    refs_total += 1
                    if cid in read_ids:
                        refs_read += 1
        seen: set = set()
        for c in tcs:
            if c.get("name") != "skill.read":
                continue
            reads += 1
            inp = c.get("input") or {}
            if inp.get("section") or (inp.get("chunk") and "#" in str(inp.get("chunk"))):
                sections += 1
            key = (inp.get("slug"), inp.get("file"), inp.get("section"), inp.get("chunk"))
            if key in seen:
                redundant += 1
            seen.add(key)
    return {
        "lifecycle.avg_input_tokens_per_turn": round(in_tok / turns, 1),
        "lifecycle.search_first_rate": (f"{search_turns}/{activ_turns}"
                                        if activ_turns else "n/a"),
        "lifecycle.section_read_rate": (f"{sections}/{reads}" if reads else "n/a"),
        "lifecycle.redundant_reads": redundant,
        "surface.avg_activation_tokens": round(sum(surf_tok) / len(surf_tok), 1) if surf_tok else 0,
        "surface.referenced_chunks_read": (f"{refs_read}/{refs_total}" if refs_total else "n/a"),
        "recovery.read_misses_recovered": (f"{recovered}/{read_misses}" if read_misses else "0/0"),
    }


# ── per-skill verdict rollup ─────────────────────────────────────────────────
def per_skill_verdict(rows_by_group: dict[str, list[dict]], slugs: list[str]) -> dict:
    """READY / NOT_READY / UNMEASURED per skill. A skill is UNMEASURED when no LLM
    group (activation/follow/funnel) produced a row for it — free gates alone are
    never READY (absence of measurement is not readiness)."""
    llm_groups = ("activation", "follow", "funnel")
    verdicts: dict = {}
    for sid in slugs:
        reasons: list[str] = []
        measured_llm = False
        for group, rows in rows_by_group.items():
            mine = [r for r in rows if r.get("skill") == sid and not r.get("diag")]
            if group in llm_groups and mine:
                measured_llm = True
            reasons += [r["id"] for r in mine if not r["ok"]]
        if reasons:
            status = "NOT_READY"
        elif not measured_llm:
            status = "UNMEASURED"
        else:
            status = "READY"
        verdicts[sid] = {"status": status, "failing": reasons}
    return verdicts
