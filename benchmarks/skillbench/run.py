#!/usr/bin/env python3
"""skillbench — the LIVE benchmark for the SDK's skill system.

How good is the skills system? This drives the REAL default ``PreactAgent`` over a
corpus of ``SKILL.md`` folders and scenarios, against a real provider, and scores —
deterministically, from the probe trace — how it **parses** a skill, **maps** it onto
the engine (stages / tools), **activates** the right one, **follows** its instructions,
and lets its content **funnel** (navigate, not dump). No LLM judge.

    python run.py --live              # run all groups, print the scorecard
    python run.py --live --report     # also write results/skillbench.html

``parse`` and ``mapping`` are deterministic (pure functions of the fixtures + the
rendered prompt) and run even on a thin scenario set; ``activation`` / ``follow`` /
``funnel`` need the model. Verdict per skill: READY / NOT_READY / UNMEASURED.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SDK_ROOT = HERE.parents[1]
sys.path.insert(0, str(SDK_ROOT))

from benchmarks._shared import compose_verdict, load_provider  # noqa: E402
from benchmarks.skillbench import scoring as sc  # noqa: E402
from benchmarks.skillbench.loader import load_skills  # noqa: E402

DATASET = HERE / "dataset"
SKILLS_DIR = DATASET / "skills"
INSTRUCTIONS = (
    "You are a capable assistant. When a skill matches the user's request, activate it "
    "and follow its procedure. Answer in the user's language."
)


def _scenarios() -> list[dict]:
    return [json.loads(x) for x in (DATASET / "scenarios.jsonl").read_text().splitlines() if x.strip()]


def _bundle_tokens(skill) -> int:
    from agent_sdk.skills import est_tokens
    return est_tokens(skill.instructions or "") + sum(
        est_tokens(c) for c in (skill.files or {}).values()
    )


class Bench:
    def __init__(self):
        self.groups: dict[str, list[dict]] = {"lint": [], "parse": [], "mapping": [],
                                               "activation": [], "follow": [], "funnel": []}
        self.metrics: dict = {}
        self.probes: list = []
        # per-skill activation tallies
        self.counts: dict[str, dict] = {}
        # over-activation accumulators (scored once, on the quality trial)
        self.overreach = 0
        self.act_scenarios = 0
        self.act_total = 0

    def add(self, group: str, rows: list[dict]) -> None:
        for r in rows:
            self.groups[group].append(r)
            print(f"  {'ok  ' if r['ok'] else 'FAIL'} {r['id']:<46} {r['detail'][:54]}")

    def tally(self, sid: str, expected: bool, activated: bool) -> None:
        c = self.counts.setdefault(sid, {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
        if expected and activated:
            c["tp"] += 1
        elif expected and not activated:
            c["fn"] += 1
        elif not expected and activated:
            c["fp"] += 1
        else:
            c["tn"] += 1


def _make_agent(client, skills):
    from agent_sdk import PreactAgent
    return PreactAgent(client=client, instructions=INSTRUCTIONS, universal_memory=False,
                       skills=skills, funnel=True, tools_in_prompt=True)


async def _run_scenario(client, scenario, by_slug, b: Bench, *, quality: bool = True):
    """Run one scenario (with its follow-up turns) and score it. ``quality=False``
    tallies activation only — used for the non-final trials when aggregating over
    ``--trials`` (so follow/funnel are recorded once, from the final trial)."""
    from agent_sdk import probe
    from agent_sdk.session import Session

    under_test = [by_slug[s] for s in scenario["skills_under_test"] if s in by_slug]
    agent = _make_agent(client, under_test)
    agent.session = Session(id=f"sb-{scenario['id']}")

    rec = await probe(agent, scenario["query"], label=f"{scenario['category']} · {scenario['id']}")
    if quality:
        b.probes.append(rec)
    # Per-turn activation: turn 0 is the initial query, turn i+1 is turns[i]. Kept as a
    # list so a `skill_switch` scenario can assert WHICH turn activated WHICH skill (the
    # union below loses that — it would mark a skill active for the whole scenario).
    per_turn = [sc.activated_slugs(rec)]
    for i, follow_up in enumerate(scenario.get("turns", []) or []):
        r = await probe(agent, follow_up, label=f"{scenario['id']} · turn{i + 1}")
        if quality:
            b.probes.append(r)
        per_turn.append(sc.activated_slugs(r))
        rec = r  # the final answer for follow checks is the last turn
    activated: set[str] = set().union(*per_turn) if per_turn else set()

    def _ondemand(slug: str) -> bool:
        sk = by_slug.get(slug)
        return sk is not None and sk.disclosure == "on_demand"

    # Activation is an ON-DEMAND decision (the model chooses to call ActivateSkill).
    # Eager skills are always inlined — not "activated" — so they are scored by
    # `follow`, not here; skip them in the activation tally.
    per_turn_exp = scenario.get("expect_activation_turns")
    if per_turn_exp:
        # tally each turn's expectation against that turn's activations
        for ti, exp in enumerate(per_turn_exp):
            if ti >= len(per_turn):
                break
            for slug, expected in (exp or {}).items():
                if _ondemand(slug):
                    b.tally(slug, bool(expected), slug in per_turn[ti])
    else:
        for slug, expected in (scenario.get("expect_activation") or {}).items():
            if _ondemand(slug):
                b.tally(slug, bool(expected), slug in activated)

    if not quality:
        return

    # Over-activation: did the model activate MORE on-demand skills than warranted?
    # warranted = the on-demand slugs the scenario expects true (across all turns).
    expected_true: set[str] = set()
    for exp in (per_turn_exp or [scenario.get("expect_activation") or {}]):
        expected_true |= {s for s, v in (exp or {}).items() if v and _ondemand(s)}
    activated_ondemand = {s for s in activated if _ondemand(s)}
    b.act_scenarios += 1
    b.act_total += len(activated_ondemand)
    if len(activated_ondemand) > len(expected_true):
        b.overreach += 1
    if scenario.get("uplift"):
        b.add("follow", sc.follow_checks(scenario, rec))

    primary = under_test[0] if under_test else None
    if primary is not None and _bundle_tokens(primary) > 0:
        b.add("funnel", sc.funnel_checks(scenario, rec, _bundle_tokens(primary)))


def _payload(rows: list[dict]) -> dict:
    # Diagnostic rows are shown but never gate the verdict.
    gating = [r for r in rows if not r.get("diag")]
    n_ok = sum(1 for r in gating if r["ok"])
    return {"checks": rows, "n": len(gating), "pass": n_ok, "all_pass": n_ok == len(gating)}


def _stamp() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


async def _amain(args) -> int:
    resolved = load_provider()
    if resolved is None:
        print("skillbench is a LIVE bench — set a provider token in packages/agent-sdk/.env "
              "(MINIMAX_API_KEY/MINIMAX_BASE_URL or ANTHROPIC_*).", file=sys.stderr)
        return 2
    from agent_sdk.clients import make_client

    model = args.model or resolved
    trials = max(1, int(args.trials))
    print(f"[skillbench] live · model={model} · trials={trials}\n")
    skills = load_skills(SKILLS_DIR)
    by_slug = {s.id: s for s in skills}
    # Adversarial fixtures (slug starts with "_") are not production skills — they
    # exist to prove the lint catches a bad skill; keep them out of the corpus verdict.
    production = [s for s in skills if not s.id.startswith("_")]
    negatives = [s for s in skills if s.id.startswith("_")]
    b = Bench()

    # ── deterministic groups (no model) ──────────────────────────────────────
    print("── lint (adversarial fixtures rejected) " + "─" * 6)
    b.add("lint", sc.lint_checks(negatives))

    print("── parse (SOP folder → structure) " + "─" * 12)
    for s in production:
        b.add("parse", sc.parse_checks(s))
    b.add("parse", [sc.search_self_locates(production, "bảo lưu reservation",
                                           "course_advisor", "reference/regulations.md")])

    print("── mapping (skill → stages / tools) " + "─" * 10)
    probe_agent = _make_agent(make_client(model), production)
    exposed = {sp["name"] for sp in (probe_agent.engine.tools.get_tool_specs()
                                     if probe_agent.engine.tools else [])}
    b.add("mapping", sc.mapping_checks(production, exposed))

    # ── live groups ──────────────────────────────────────────────────────────
    print("── activation / follow / funnel (live) " + "─" * 6)
    client = make_client(model)
    for scenario in _scenarios():
        for t in range(trials):
            await _run_scenario(client, scenario, by_slug, b, quality=(t == trials - 1))
    act_rows, act_metrics = sc.activation_checks(b.counts)
    b.add("activation", act_rows)
    b.metrics.update(act_metrics)
    b.metrics.update(sc.overreach_metrics(b.overreach, b.act_scenarios, b.act_total))

    # ── verdict ───────────────────────────────────────────────────────────────
    payloads = {g: (_payload(rows) if rows else None) for g, rows in b.groups.items()}
    verdict = compose_verdict(payloads)
    verdict.setdefault("metrics", {}).update(b.metrics)
    verdict["metrics"].update(sc.lifecycle_metrics(b.probes))  # token/lifecycle efficiency
    per_skill = sc.per_skill_verdict(b.groups, [s.id for s in production])

    print("\n── per-skill verdict " + "─" * 24)
    for sid, v in sorted(per_skill.items()):
        print(f"  {v['status']:<11} {sid}" + (f"  ({len(v['failing'])} failing)" if v["failing"] else ""))
    total = sum(len(r) for r in b.groups.values())
    ok = sum(1 for rows in b.groups.values() for r in rows if r["ok"])
    print(f"\nskillbench: {ok}/{total} checks pass · verdict {verdict['status']}")

    if args.report is not None:
        from agent_sdk.viewer import write_viewer

        modes = {g: p for g, p in payloads.items() if p is not None}
        # surface the per-skill rollup as report metrics
        verdict["metrics"].update({f"verdict.{k}": v["status"] for k, v in per_skill.items()})
        # The full-detail interactive viewer (Timeline/Flow/Lobes/Context/Prompt/
        # Raw-JSON per turn), with the readiness OVERVIEW (verdict + per-group
        # check tables) as a banner above it — one self-contained HTML.
        out = write_viewer(args.report, b.probes, label=f"skillbench · live · {model}",
                           verdict=verdict, modes=modes)
        print(f"report: {out}")
    return 0 if verdict["status"] == "READY" else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="acknowledge real provider calls (required)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--trials", type=int, default=1,
                    help="run each scenario N times; activation is pooled across trials "
                         "(reduces live model variance). Default 1.")
    ap.add_argument("--report", nargs="?", type=Path, default=None,
                    const=HERE / "results" / "skillbench.html")
    args = ap.parse_args()
    if not args.live:
        print("skillbench only runs live. Pass --live (it makes real provider calls).", file=sys.stderr)
        return 2
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
