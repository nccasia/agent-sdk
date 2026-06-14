#!/usr/bin/env python3
"""corgictionbech — the deterministic gate for the SDK's METACOGNITION layer.

Certifies monitor→regulate self-regulation: the decision table (precedence, thresholds), the
apply/observe channel, and the pinned-step guard (``cite``/``filter`` are never skippable). Ported
from the monorepo corgictionbech onto the agent-sdk public surface; leaf-pure. **FREE / deterministic
— no provider, no LLM judges the pipeline** (the whole point: metacognition is a pure function of the
engine snapshot). See ``METHOD.md``.

    python benchmarks/corgictionbech/run.py            # the four deterministic modes
    python benchmarks/corgictionbech/run.py --report   # + results/corgictionbech.html
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from agent_sdk import PINNED_LOBES  # noqa: E402
from agent_sdk.inspection import (  # noqa: E402
    EngineSnapshot,
    FlowAxisSnapshot,
    FlowStepInspection,
    LobeAxisSnapshot,
    LobeInspection,
)
from agent_sdk.metacognition import MetaController, MetaObservation, monitor, regulate  # noqa: E402
from benchmarks._shared import compose_verdict, load_provider  # noqa: E402
from benchmarks.corgictionbech import scoring  # noqa: E402

RESULTS = HERE / "results"
DATASET = HERE / "dataset" / "scenarios.jsonl"


def _ck(cid: str, ok: bool, detail: str) -> dict:
    return {"id": cid, "ok": bool(ok), "detail": detail}


def _payload(checks: list[dict], metrics: dict | None = None) -> dict:
    return {"checks": checks, "n": len(checks), "pass": sum(c["ok"] for c in checks),
            "all_pass": all(c["ok"] for c in checks) and bool(checks), "metrics": metrics or {}}


def _obs(kind: str, target: str, sev: float) -> MetaObservation:
    return MetaObservation(id=f"t:{target}:{kind}", kind=kind, target=target, severity=sev,
                           detail=f"{kind} on {target}")


# ── monitor: snapshots → the right observations ────────────────────────────────────────────────
def run_monitor() -> dict:
    fa = FlowAxisSnapshot(flow="research", disabled=False, steps=[
        FlowStepInspection(flow="research", step="plan", loop="single", tools=[],
                           lobes=[], state_nodes=[]),                                  # empty_lobe_slice
        FlowStepInspection(flow="research", step="synthesize", loop="single", tools=[],
                           lobes=["synthesize"], disabled=True, state_nodes=[]),       # step_disabled
        FlowStepInspection(flow="research", step="cite", loop="none", tools=[],
                           lobes=["cite"], state_nodes=[{"id": "context:tight", "activated": True}]),
    ])
    eng = EngineSnapshot(path={"name": "qna", "score": 0.4, "emergent": False},
                         flow_steps=[{"flow": "research", "step": "research", "node_count": 0}])
    la = LobeAxisSnapshot(lobes=[LobeInspection(id="memory_recall", layer=2, activated=False,
                                                state_nodes=[{"id": "mem", "activated": False}])],
                          activated=[])
    kinds = {o.kind for o in monitor(flow_axis=fa, engine=eng, lobe_axis=la)}
    want = {"empty_lobe_slice", "step_disabled", "context_tight", "low_confidence_path",
            "empty_step_context", "inactive_lobe_group"}
    checks = [_ck(f"monitor.{k}", k in kinds, "observed" if k in kinds else "MISSING") for k in sorted(want)]
    return _payload(checks)


# ── regulate: observations → the decision table (precedence + thresholds) ───────────────────────
def run_regulate() -> dict:
    cases = [
        ("healthy_continue", (), "synthesize", ("synthesize",), "continue"),
        ("low_conf_review", (_obs("low_confidence_path", "qna", 0.7),), "synthesize", (), "meta_review"),
        ("tight_adjust", (_obs("context_tight", "research.synthesize", 0.75),), "synthesize",
         ("synthesize", "memory_recall", "skill_select"), "adjust_lobe_slice"),
        ("empty_skip", (_obs("empty_lobe_slice", "research.plan", 0.8),), "plan", ("plan",), "skip_step"),
        ("empty_step_retry", (_obs("empty_step_context", "research.research", 0.65),), "research",
         ("research",), "retry_step"),
        ("precedence_review", (_obs("empty_lobe_slice", "research.plan", 0.8),
                               _obs("low_confidence_path", "qna", 0.7)), "plan", ("plan",), "meta_review"),
    ]
    checks = []
    for cid, obs, step, lobes, want in cases:
        d = regulate(obs, target_flow="research", target_step=step, current_lobes=lobes)
        ok = d.action == want
        if cid == "tight_adjust":  # trims the optional recall/skill lobes, keeps the step lobe
            ok = ok and "memory_recall" not in d.target_lobes and "synthesize" in d.target_lobes
        checks.append(_ck(f"regulate.{cid}", ok, f"action={d.action} (want {want})"))
    return _payload(checks)


# ── pinned guard: cite/filter empty slice → meta_review, NEVER skip_step ─────────────────────────
def run_pinned() -> dict:
    checks = []
    for step in sorted(PINNED_LOBES):
        d = regulate((_obs("empty_lobe_slice", f"qna.{step}", 0.8),),
                     target_flow="qna", target_step=step, current_lobes=())
        checks.append(_ck(f"pinned.{step}_never_skipped", d.action == "meta_review",
                          f"action={d.action} (pinned step must escalate, not skip)"))
    return _payload(checks, {"pinned_steps": len(PINNED_LOBES)})


# ── channel: apply/observe + the action allowlist ───────────────────────────────────────────────
def run_channel() -> dict:
    apply = MetaController(mode="apply")
    observe = MetaController(mode="observe")
    widened = MetaController(mode="apply", apply_actions=frozenset({"adjust_lobe_slice", "skip_step"}))
    checks = [
        _ck("channel.apply_default_trim", apply.should_apply("adjust_lobe_slice"),
            "apply mode applies the default trim action"),
        _ck("channel.apply_withholds_skip", not apply.should_apply("skip_step"),
            "skip_step needs an explicit allowlist (not default)"),
        _ck("channel.observe_never_mutates", not observe.should_apply("adjust_lobe_slice"),
            "observe is the floor — monitors but never mutates"),
        _ck("channel.allowlist_widens", widened.should_apply("skip_step"),
            "an explicit allowlist enables skip_step"),
    ]
    return _payload(checks)


# ── plugin surface (deterministic): the MetacognitionPlugin matches the implementation ───────────
def run_plugin_surface() -> dict:
    """The shipped plugin assembles its surface and its tool enactors write the right turn-state
    keys (reason → write → enact); cite/filter are never reshapeable. Free — no provider."""
    return _payload(scoring.plugin_surface_checks())


# ── live (single-arm): the equipped agent makes the right meta choices ────────────────────────────
def _load_scenarios() -> list[dict]:
    return [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]


def _make_agent(model):
    from agent_sdk import PreactAgent
    from agent_sdk.clients import make_client
    from agent_sdk.plugins.metacognition import MetacognitionPlugin

    return PreactAgent(client=make_client(model), plugins=[MetacognitionPlugin()], metacognition="apply")


async def run_live(model: str, trials: int) -> tuple[dict, list]:
    """Measure the EQUIPPED agent (the best configuration): it answers every scenario correctly
    (the gate), and we record how often it reaches for the expected meta lever (decision_hit_rate,
    transparency — NOT a gate, since forcing a lever on a trivial turn would be overreach)."""
    from agent_sdk import probe

    agent = _make_agent(model)
    gate_checks: list[dict] = []  # correctness of the best configuration — the live gate
    rows: list[dict] = []
    probes: list = []
    lever_hits = lever_total = 0
    for sc in _load_scenarios():
        # pool decision quality across trials (live variance); correctness from the last trial
        dq_ok, dq_detail, rec = False, "", None
        for t in range(trials):
            rec = await probe(agent, sc["query"], label=f"{sc['id']}·t{t}")
            probes.append(rec)
            ok, dq_detail = scoring.decision_quality(rec, sc["expect"])
            dq_ok = dq_ok or ok
        if not sc["expect"].get("control"):  # lever scenario — record hit-rate (non-gating)
            lever_total += 1
            lever_hits += int(dq_ok)
            print(f"  [{'lever' if dq_ok else ' -- '}] decision.{sc['id']:<22} {dq_detail}")
        gate_checks.append(_ck(f"answer.{sc['id']}", scoring.answered_correctly(rec, sc["expect"]),
                               f"answer ok={scoring.answered_correctly(rec, sc['expect'])}"))
        rows.append(scoring.live_row(rec, sc["expect"]))
    metrics = scoring.live_metrics(rows)
    if lever_total:
        metrics["decision_hit_rate"] = round(lever_hits / lever_total, 3)
    return _payload(gate_checks, metrics), probes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true", help="write results/corgictionbech.html")
    ap.add_argument("--label", default="base")
    ap.add_argument("--live", action="store_true",
                    help="also run the live measurement of the equipped agent (needs a provider token)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--trials", type=int, default=1)
    args = ap.parse_args()

    # Deterministic floor — the kernel monitor→regulate table + the shipped plugin surface.
    # These always run (no provider) and keep the bench READY in the no-cred ladder.
    payloads: dict[str, dict | None] = {
        "monitor": run_monitor(), "regulate": run_regulate(),
        "pinned": run_pinned(), "channel": run_channel(),
        "plugin_surface": run_plugin_surface(),
    }
    probes: list = []
    if args.live:
        resolved = load_provider()
        if resolved is None:
            print("[corgictionbech] --live given but no provider token — running the deterministic "
                  "floor only (live A/B skipped).", file=sys.stderr)
        else:
            model = args.model or resolved
            trials = max(1, int(args.trials))
            print(f"[corgictionbech] live (equipped agent) · model={model} · trials={trials}\n")
            live_payload, probes = asyncio.run(run_live(model, trials))
            payloads["live"] = live_payload

    verdict = compose_verdict(
        payloads,
        record={"pinned": ["pinned_steps"],
                "live": ["accuracy", "decision_hit_rate", "meta_tokens_avg"]},
    )

    print("── corgictionbech ─────────────────────────────────────────────")
    total = ok = 0
    for p in payloads.values():
        if p is None:
            continue
        for c in p["checks"]:
            print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['id']:<34} {c['detail'][:48]}")
        total += p["n"]
        ok += p["pass"]
    print(f"\ncorgictionbech: {ok}/{total} checks pass · verdict {verdict['status']}")
    if verdict["metrics"]:
        print("metrics:", verdict["metrics"])
    if verdict["reasons"]:
        print("reasons:", "; ".join(verdict["reasons"]))

    if args.report:
        from agent_sdk.viewer import write_viewer

        RESULTS.mkdir(exist_ok=True)
        out = RESULTS / "corgictionbech.html"
        modes = {m: p for m, p in payloads.items() if p is not None}
        label = (f"corgictionbech · live · {args.model or ''}" if probes
                 else "corgictionbech · metacognition")
        # One unified two-page report (Overview + Inspect); Inspect is the live probe
        # detail when present, an empty-state on a deterministic floor-only run.
        write_viewer(out, probes, label=label, verdict=verdict, modes=modes)
        print(f"report: {out}")

    return 0 if verdict["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
