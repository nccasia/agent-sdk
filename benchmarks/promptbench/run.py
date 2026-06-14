#!/usr/bin/env python3
"""promptbench — the gate for the SDK's PROMPTS: are they well-structured AND well-written?

Three tiers, free → live:

1. **structure** (free, deterministic) — the composed system prompt is well-layered: a stable
   instruction prefix leads and the turn-volatile sections form a contiguous tail (the cache-prefix
   boundary); identity appears once and first; ``<env>`` is last; no section/persona/conversation
   duplication. Read off the probe's ``system_segments`` (no LLM).
2. **quality** (free, deterministic) — a rule-based lint of the SDK's authored prompt constants
   against the prompt-engineering best practices in docs/concepts/14: one role only, no double
   negatives, an explicit output/action directive, no ALL-CAPS shouting, bounded length. Emits a
   per-prompt quality score.
3. **judge** (live, ``--live``) — an LLM scores each authored prompt on a rubric (clarity /
   specificity / consistency / output-contract, 1–5). Skipped cleanly with no provider, so the
   free tiers still gate in the no-cred ladder.

    python benchmarks/promptbench/run.py            # the free tiers (structure + quality)
    python benchmarks/promptbench/run.py --live      # + the LLM-judge tier
    python benchmarks/promptbench/run.py --report     # + results/promptbench.html
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from agent_sdk import PreactAgent, probe  # noqa: E402
from agent_sdk.clients import FakeClient  # noqa: E402
from benchmarks._shared import compose_verdict, emit_report, load_provider  # noqa: E402

RESULTS = HERE / "results"

SCENARIOS = [
    ("qna", "what is the capital of France?"),
    ("research", "compare React and Vue in depth and cite sources"),
    ("clarify", "what about that one?"),
    ("relational", "hello there!"),
]

_SINGLETON = {"instructions", "memory_directive", "stage_prompt", "tools", "skills",
              "grounding", "datetime", "respond"}
_STAB = {"stable": 0, "slow": 1, "turn": 2, "volatile": 3}
_PERSONA_RE = re.compile(r"\byou are\b", re.IGNORECASE)
# "never … without …", "do not … unless", "cannot … un-" — negation stacked on negation.
_DOUBLE_NEG_RE = re.compile(r"\b(never|not|cannot|don't|do not)\b[^.\n]*\b(without|unless|un\w+)\b",
                            re.IGNORECASE)
_DIRECTIVE_RE = re.compile(
    r"\b(output|respond|return|answer|write|rewrite|classify|verify|summari|"
    r"produce|reply|drop|keep|use|apply|plan)\b", re.IGNORECASE)
_ALLCAPS_RE = re.compile(r"\b[A-Z]{4,}\b")


def _ck(cid: str, ok: bool, detail: str) -> dict:
    return {"id": cid, "ok": bool(ok), "detail": detail}


def _payload(checks: list[dict], metrics: dict | None = None) -> dict:
    return {"checks": checks, "n": len(checks), "pass": sum(c["ok"] for c in checks),
            "all_pass": all(c["ok"] for c in checks) and bool(checks), "metrics": metrics or {}}


# ── the SDK's authored prompt constants (the prompts under evaluation) ───────────────────────────
def _authored_prompts() -> dict[str, str]:
    """Collect the shipped prompt constants. A missing one is skipped (the surface evolves)."""
    out: dict[str, str] = {}

    def add(name: str, getter):
        try:
            val = getter()
            if isinstance(val, str) and val.strip():
                out[name] = val
        except Exception:
            pass

    from agent_sdk.cognition.lobes import classify, condense, plan, research, synthesize
    from agent_sdk.expression.lobes import respond

    def _attr(module_paths: list[str], attr: str):
        """Pull an attribute from the first importable module path — resilient to plugin
        migrations (e.g. the grounding lobes moving safety/ → rag/)."""
        for mp in module_paths:
            try:
                return getattr(__import__(mp, fromlist=[attr]), attr)
            except Exception:
                continue
        raise AttributeError(attr)

    add("synthesize.SYSTEM", lambda: synthesize.SYSTEM_PROMPT)
    add("synthesize.SIMPLE", lambda: synthesize.SIMPLE_SYSTEM_PROMPT)
    add("respond.SYSTEM", lambda: respond.SYSTEM_PROMPT)
    add("cite.SYSTEM", lambda: _attr(
        ["agent_sdk.plugins.rag.lobes.cite", "agent_sdk.plugins.rag.citation",
         "agent_sdk.plugins.safety.lobes.cite"], "SYSTEM_PROMPT"))
    add("filter.SYSTEM", lambda: _attr(
        ["agent_sdk.plugins.rag.lobes.filter", "agent_sdk.plugins.rag.citation",
         "agent_sdk.plugins.safety.lobes.filter"], "SYSTEM_PROMPT"))
    add("format.SYSTEM", lambda: _attr(["agent_sdk.plugins.format.lobes.format"], "SYSTEM_PROMPT"))
    add("classify.SYSTEM", lambda: classify.SYSTEM_PROMPT)
    add("condense.SYSTEM", lambda: condense.SYSTEM_PROMPT)
    add("plan.SYSTEM", lambda: plan.SYSTEM_PROMPT)
    add("research.SYSTEM", lambda: research.SYSTEM_PROMPT)
    add("memory_directive",
        lambda: __import__("agent_sdk.agent", fromlist=["MEMORY_DIRECTIVE"]).MEMORY_DIRECTIVE)
    add("plan_prompt",
        lambda: __import__("agent_sdk.plugins.planning.stages", fromlist=["_PLAN_PROMPT"])._PLAN_PROMPT)
    return out


# ── tier 1: structure (free) ─────────────────────────────────────────────────────────────────────
async def _probe_scenarios() -> list:
    """One probed turn per scenario — the ProbeRecords feed both the structure tier and the report's
    Inspect timeline (each turn's stages + composed prompt + provenance)."""
    recs = []
    for label, q in SCENARIOS:
        agent = PreactAgent(client=FakeClient(["ok"] * 8),
                            instructions="You are a helpful research assistant.")
        recs.append(await probe(agent, q, label=label))
    return recs


def _stages(records: list) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for rec in records:
        for st in rec.stages:
            out.append((f"{rec.label}/{st.get('stage', '?')}", st))
    return out


def run_structure(stages: list[tuple[str, dict]]) -> dict:
    rules: dict[str, list[str]] = {
        "identity.once": [], "identity.single_persona": [], "dedup.source_unique": [],
        "ordering.identity_first": [], "ordering.env_last": [], "ordering.volatile_tail": [],
        "convo.not_duplicated": [], "coverage.valid_offsets": [],
    }
    n_segs = 0
    for label, st in stages:
        segs = st.get("system_segments") or []
        text = st.get("system_prompt") or ""
        sources = [s["source"] for s in segs]
        n_segs += len(segs)
        if sources.count("instructions") > 1:
            rules["identity.once"].append(label)
        if len(_PERSONA_RE.findall(text)) > 1:
            rules["identity.single_persona"].append(label)
        named = [s for s in sources if s in _SINGLETON]
        if len(named) != len(set(named)):
            rules["dedup.source_unique"].append(label)
        if "instructions" in sources and sources[0] != "instructions":
            rules["ordering.identity_first"].append(label)
        if "datetime" in sources and sources[-1] != "datetime":
            rules["ordering.env_last"].append(label)
        tail = False
        for s in segs:
            if _STAB.get(s.get("stability", "stable"), 1) >= 2:
                tail = True
            elif tail:
                rules["ordering.volatile_tail"].append(label)
                break
        if "conversation" in sources:
            rules["convo.not_duplicated"].append(label)
        last = 0
        for s in segs:
            if s["start"] < last or s["end"] > len(text) or s["start"] >= s["end"]:
                rules["coverage.valid_offsets"].append(label)
                break
            last = s["end"]
    checks = [_ck(cid, not bad, "clean" if not bad else f"{len(bad)} bad: {bad[:4]}")
              for cid, bad in rules.items()]
    return _payload(checks, {"stages": len(stages), "segments": n_segs})


# ── tier 2: quality (free, rule-based lint) ──────────────────────────────────────────────────────
def _lint(text: str) -> list[str]:
    """Return the quality rules a prompt VIOLATES (empty = clean) — see docs/concepts/14."""
    bad: list[str] = []
    if len(_PERSONA_RE.findall(text)) > 1:
        bad.append("multi_role")             # ≥2 "You are …" — declare one identity only
    if _DOUBLE_NEG_RE.search(text):
        bad.append("double_negative")        # negation stacked on negation — hard to parse
    if not _DIRECTIVE_RE.search(text):
        bad.append("no_directive")           # no imperative / output instruction
    if len(set(_ALLCAPS_RE.findall(text))) > 6:
        bad.append("allcaps_shouting")       # emphasis everywhere = emphasis nowhere
    if len(text) > 3000:
        bad.append("too_long")               # a single contribution should stay bounded
    return bad


def run_quality(prompts: dict[str, str]) -> dict:
    checks, scored = [], []
    for name, text in prompts.items():
        bad = _lint(text)
        scored.append(1 - len(bad) / 5)      # 5 rules → fraction clean
        checks.append(_ck(f"quality.{name}", not bad, "clean" if not bad else f"violates {bad}"))
    avg = round(sum(scored) / len(scored), 3) if scored else 0.0
    return _payload(checks, {"prompts": len(prompts), "quality_avg": avg})


# ── tier 3: judge (live, LLM-as-judge) ───────────────────────────────────────────────────────────
_RUBRIC = (
    "You are a strict prompt-engineering reviewer. Score the PROMPT below on each dimension from 1 "
    "(poor) to 5 (excellent):\n"
    "- clarity: unambiguous, easy to follow\n"
    "- specificity: concrete instructions, not vague\n"
    "- consistency: no self-contradiction\n"
    "- output_contract: states the expected output / format\n"
    'Respond with ONLY a JSON object: {"clarity":N,"specificity":N,"consistency":N,'
    '"output_contract":N,"note":"<=12 words"}.'
)
_DIMS = ("clarity", "specificity", "consistency", "output_contract")


async def run_judge(prompts: dict[str, str], model: str, *, floor: float = 3.5) -> dict:
    from agent_sdk.clients import make_client
    from agent_sdk.lobes.runtime import extract_text

    client = make_client(model)
    checks, means = [], []
    for name, text in prompts.items():
        try:
            # Budget headroom: a thinking model (MiniMax) spends tokens reasoning before the JSON,
            # so a tight max_tokens truncates the answer away. Give it room.
            msg = await client(stage="judge", system=_RUBRIC,
                               messages=[{"role": "user", "content": f"PROMPT:\n{text}"}],
                               max_tokens=800, temperature=0.0, count_usage=False)
            m = re.search(r"\{.*\}", extract_text(msg) or "", re.S)
            if m is None:
                checks.append(_ck(f"judge.{name}", False, "no JSON in judge reply"))
                continue
            data = json.loads(m.group(0))
            scores = [min(5.0, max(0.0, float(data.get(d, 0)))) for d in _DIMS]
            mean = round(sum(scores) / len(scores), 2)
            means.append(mean)
            checks.append(_ck(f"judge.{name}", mean >= floor,
                              f"mean={mean} {dict(zip(_DIMS, scores, strict=True))} · "
                              f"{str(data.get('note', ''))[:32]}"))
        except Exception as exc:  # a judge miss shouldn't crash the tier
            checks.append(_ck(f"judge.{name}", False, f"judge error: {type(exc).__name__}"))
    agg = round(sum(means) / len(means), 3) if means else 0.0
    weak = [c["id"].split(".", 1)[1] for c in checks if not c["ok"]]
    metrics = {"judge_mean": agg, "floor": floor, "weak": weak}
    payload = _payload(checks, metrics)
    # The judge reads each prompt OUT OF CONTEXT (no identity/stage/user message) and is
    # non-deterministic, so a single fragment's low score is noisy. Gate on the AGGREGATE mean;
    # the per-prompt rows stay visible as an evaluation of where the prompts are weak.
    payload["all_pass"] = agg >= floor
    return payload


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true", help="write results/promptbench.html")
    ap.add_argument("--label", default="base")
    ap.add_argument("--live", action="store_true", help="also run the LLM-judge tier (needs a provider)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--trials", type=int, default=1)
    args = ap.parse_args()

    records = await _probe_scenarios()
    stages = _stages(records)
    prompts = _authored_prompts()
    payloads: dict[str, dict | None] = {
        "structure": run_structure(stages),
        "quality": run_quality(prompts),
    }
    if args.live:
        resolved = load_provider()
        if resolved is None:
            print("[promptbench] --live given but no provider token — running the free tiers only.",
                  file=sys.stderr)
        else:
            model = args.model or resolved
            print(f"[promptbench] judge · model={model} · {len(prompts)} prompts\n")
            payloads["judge"] = await run_judge(prompts, model)

    verdict = compose_verdict(payloads, record={"quality": ["quality_avg"], "judge": ["judge_mean"]})

    print("── promptbench ────────────────────────────────────────────────")
    total = ok = 0
    for p in payloads.values():
        if p is None:
            continue
        for c in p["checks"]:
            print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['id']:<28} {c['detail'][:50]}")
        total += p["n"]
        ok += p["pass"]
    print(f"\npromptbench: {ok}/{total} checks pass · verdict {verdict['status']}")
    if verdict["metrics"]:
        print("metrics:", verdict["metrics"])
    if verdict["reasons"]:
        print("reasons:", "; ".join(verdict["reasons"]))

    if args.report:
        from agent_sdk.viewer import write_viewer

        RESULTS.mkdir(exist_ok=True)
        modes = {m: p for m, p in payloads.items() if p is not None}
        write_viewer(RESULTS / "promptbench.html", records, label="promptbench · prompt quality",
                     verdict=verdict, modes=modes)
        html, md = emit_report(HERE, "promptbench", label="promptbench · prompt quality",
                               verdict=verdict, modes=modes, probes=records)
        print(f"report: {RESULTS / 'promptbench.html'}\ncommitted: {md} · {html}")
    return 0 if verdict["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
