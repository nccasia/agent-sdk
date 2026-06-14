#!/usr/bin/env python3
"""skillbench compare — does the compiled skill surface improve token / accuracy / cost?

Runs the bundle scenarios (the skills big enough that the surface matters) under each
skill-handling VARIANT and prints a token/accuracy/cost table so you can pick the best
setting:

  off          ActivateSkill returns the raw body / file ToC (the pre-surface baseline)
  deterministic  a no-LLM chunk-index surface
  llm@N        the LLM-compiled core + chunk refs, surface budget N tokens

    uv --directory packages/agent-sdk run python benchmarks/skillbench/compare.py --live

Per variant: total input/output tokens (the ongoing per-turn cost), the activation
surface size, the one-time compile cost, and ACCURACY (did it activate + answer
correctly). The recommendation = highest accuracy, then fewest tokens.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SDK_ROOT = HERE.parents[1]
sys.path.insert(0, str(SDK_ROOT))

from benchmarks._shared import load_provider  # noqa: E402
from benchmarks.skillbench.loader import load_skills  # noqa: E402
from benchmarks.skillbench.run import INSTRUCTIONS, SKILLS_DIR, _scenarios  # noqa: E402

# (label, surface_mode, budget). Small skills are unaffected (within budget = body);
# the variants only differ on the bundle skills, so we run the bundle scenarios.
VARIANTS = [
    ("off", "off", 600),
    ("deterministic", "deterministic", 600),
    ("llm@300", "llm", 300),
    ("llm@600", "llm", 600),
    ("llm@900", "llm", 900),
]

# Accuracy oracle for the course_advisor bundle scenarios: the answer is correct if it
# contains ANY of these (the grounded fact the right chunk holds).
EXPECT: dict[str, list[str]] = {
    # course_advisor — compact SKILL.md map + LARGE reference files (the surface
    # should reduce to the authored body; compiling adds overhead).
    "ca-clear-regulations-01": ["two semesters", "7 working", "bảo lưu", "reserve"],
    "ca-clear-pricing-01": ["6,000,000", "professional", "two installments", "50%"],
    "ca-disclosure-catalog-01": ["ML401", "prerequisite", "ML402", "DA302"],
    "ca-paraphrase-01": ["14 day", "non-refundable", "no tuition refund", "fee-free"],
    # billing_policy — LARGE unstructured-ish BODY, no files (the case the LLM
    # surface should WIN: a compact core vs dumping the whole body).
    "bp-dispute-01": ["48 hour", "forty-eight", "tier two", "tier 2"],
    "bp-refund-01": ["prorat", "unused"],
    "bp-vip-01": ["skip", "straight to tier", "tier two", "sixty", "60 day"],
}


def _est(t):
    from agent_sdk.skills import est_tokens
    return est_tokens(t or "")


async def _run_variant(mode, budget, scenarios, by_slug, model):
    from agent_sdk import PreactAgent, probe
    from agent_sdk.clients import make_client
    from agent_sdk.session import Session

    # ONE agent per variant (cache warms after the first activation — the realistic,
    # amortized cost). persist=False so A/B runs never write fixture sidecars.
    budgets = {"skill_surface_mode": mode, "skill_surface_budget": budget,
               "skill_surface_persist": False}
    under = sorted({s for sc in scenarios for s in sc["skills_under_test"] if s in by_slug})
    agent = PreactAgent(client=make_client(model), instructions=INSTRUCTIONS,
                        universal_memory=False, skills=[by_slug[s] for s in under],
                        funnel=True, tools_in_prompt=True, budgets=budgets)

    in_tok = out_tok = correct = activated_ok = 0
    surf: list[int] = []
    full_reads = sec_reads = 0
    for sc in scenarios:
        agent.session = Session(id=f"cmp-{mode}-{budget}-{sc['id']}")
        rec = await probe(agent, sc["query"], label=sc["id"])
        for c in rec.llm_calls:
            u = c.get("usage") or {}
            in_tok += int(u.get("input_tokens", 0) or 0)
            out_tok += int(u.get("output_tokens", 0) or 0)
        tcs = rec.tool_calls
        acts = {(c.get("input") or {}).get("slug") for c in tcs if c.get("name") == "ActivateSkill"}
        want = {k for k, v in (sc.get("expect_activation") or {}).items() if v}
        if want & acts:
            activated_ok += 1
        for c in tcs:
            if c.get("name") == "ActivateSkill":
                surf.append(_est(c.get("output")))
            if c.get("name") == "skill.read":
                x = c.get("input") or {}
                if x.get("section") or (x.get("chunk") and "#" in str(x.get("chunk"))):
                    sec_reads += 1
                else:
                    full_reads += 1
        ans = (rec.answer or "").lower()
        if any(t.lower() in ans for t in EXPECT.get(sc["id"], [])):
            correct += 1
    n = len(scenarios)
    return {
        "in_tok": in_tok, "out_tok": out_tok, "total_tok": in_tok + out_tok,
        "avg_surface_tok": round(sum(surf) / len(surf), 1) if surf else 0,
        "accuracy": f"{correct}/{n}", "accuracy_pct": round(100 * correct / n, 1) if n else 0.0,
        "activation": f"{activated_ok}/{n}", "section_reads": sec_reads, "full_reads": full_reads,
    }


async def _compile_cost(pack, budget, model):
    """One-time compile cost (output tokens) to build this skill's surface."""
    from agent_sdk.clients import make_client
    from agent_sdk.skills.compiler import _COMPILE_PROMPT, _chunk_index, chunk_skill
    if _est(pack.instructions) <= budget:
        return 0  # body fits the budget → no LLM compile happens
    client = make_client(model)
    user = f"BODY:\n{pack.instructions}\n\nCHUNKS:\n{_chunk_index(chunk_skill(pack))}"
    try:
        msg = await client(stage="skill.compile", system=_COMPILE_PROMPT.format(budget=budget),
                           messages=[{"role": "user", "content": user}],
                           max_tokens=max(2048, budget * 3), temperature=0.0)
        return int(getattr(getattr(msg, "usage", None), "output_tokens", 0) or 0)
    except Exception:
        return 0


async def _amain(args) -> int:
    model = args.model or load_provider()
    if model is None:
        print("compare is LIVE — set a provider token in packages/agent-sdk/.env", file=sys.stderr)
        return 2
    skills = load_skills(SKILLS_DIR)
    by_slug = {s.id: s for s in skills}
    # the curated eval set (EXPECT) — covers both shapes: compact-map+files
    # (course_advisor) and large-body-no-files (billing_policy).
    all_scens = [s for s in _scenarios() if s["id"] in EXPECT]
    by_skill: dict[str, list] = {}
    for s in all_scens:
        by_skill.setdefault(s["skills_under_test"][0], []).append(s)
    print(f"[compare] live · model={model} · {len(all_scens)} scenarios · {len(VARIANTS)} variants\n")

    # Per skill, so the shape-dependent result is visible (a compact-map skill vs a
    # large-body skill have different best settings).
    for skill_id, scens in by_skill.items():
        from agent_sdk.skills.parser import est_tokens
        body_tok = est_tokens(by_slug[skill_id].instructions)
        nfiles = len(by_slug[skill_id].files or {})
        print(f"── {skill_id}  (body ~{body_tok} tok, {nfiles} files, {len(scens)} scenarios) ──")
        rows = []
        for label, mode, budget in VARIANTS:
            r = await _run_variant(mode, budget, scens, by_slug, model)
            r["compile_out_tok"] = (await _compile_cost(by_slug[skill_id].to_pack(), budget, model)
                                    if mode == "llm" else 0)
            r["label"] = label
            rows.append(r)
            print(f"  {label:<14} acc {r['accuracy']:<5} surface ~{r['avg_surface_tok']:>5} tok · "
                  f"in {r['in_tok']:>6} out {r['out_tok']:>5} (1×compile +{r['compile_out_tok']}) · "
                  f"reads {r['section_reads']}sec/{r['full_reads']}full")
        best = max(rows, key=lambda r: (r["accuracy_pct"], -r["total_tok"]))
        base = next((r for r in rows if r["label"] == "off"), None)
        delta = ""
        if base and base["total_tok"] and best["label"] != "off":
            save = round(100 * (base["total_tok"] - best["total_tok"]) / base["total_tok"], 1)
            delta = f"  ({save:+.1f}% turn tokens vs off)"
        print(f"  → BEST: {best['label']}  acc {best['accuracy']}, {best['total_tok']} turn tok{delta}\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="acknowledge real provider calls (required)")
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    if not args.live:
        print("compare only runs live. Pass --live.", file=sys.stderr)
        return 2
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
