#!/usr/bin/env python3
"""flowbench — the gate for the SDK's FLOW axis (OX): are ALL the flows wired + working?

Proves every default flow end-to-end, deterministically (no provider):

1. **routing** — each query routes to the right path + the right flow-qualified state sequence
   (read via the no-LLM ``inspect``), including adversarial near-neighbours.
1b. **tiers** — each flow maps to a complexity tier (direct/standard/deep/steward) and the tier fixes
   the grounding contract; the whole simple→complex spectrum is represented.
2. **states** — every stage id is one of the canonical reasoning states
   (``understand/explore/plan/act/synthesize/cite/filter/respond``) and they appear in canonical order.
3. **grounding** — the deep (``research``) flow carries the pinned ``cite → filter`` contract; the
   social/standard flows do not.
4. **execution** — each flow actually RUNS (a ``FakeClient`` probe): the executed stages match the
   declared sequence and the turn answers (or refuses, when grounded with no sources).
5. **coverage** — every default flow (``qna/research/clarify/relational/fallback/onboarding``) is
   exercised by at least one scenario — no flow goes untested.
6. **determinism** — routing is a pure function of (spec, context).
7. **subject** — a state instantiated against a subject threads it into the composed prompt.

    python benchmarks/flowbench/run.py            # the deterministic flow-axis modes
    python benchmarks/flowbench/run.py --report   # + results/flowbench.html
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from agent_sdk import PreactAgent, probe  # noqa: E402
from agent_sdk.clients import FakeClient  # noqa: E402
from agent_sdk.session import Session, SessionState, Turn  # noqa: E402
from agent_sdk.stores.session import SessionStoreInMemory  # noqa: E402
from agent_sdk.viewer import write_viewer  # noqa: E402
from benchmarks._shared import compose_verdict, emit_report  # noqa: E402

RESULTS = HERE / "results"

# The canonical reasoning states (docs/concepts/15) + their order. Every default stage id must be
# one of these, and a flow's states must appear in non-decreasing canonical order.
_CANONICAL = ("understand", "explore", "plan", "act", "synthesize", "cite", "filter", "respond")
_ORDER = {s: i for i, s in enumerate(_CANONICAL)}

# Every default flow, with the query that routes to it, its complexity TIER, the expected
# flow-qualified state sequence, whether it is grounded, and how to trigger it (``warmup`` seeds
# clarify's history; ``config_mode`` flags onboarding). One entry per flow proves it works; the
# ``adversarial`` entries are near-neighbour routing pressure (a question wearing a greeting, an
# imperative knowledge ask). ``tier`` ∈ direct (cheap/social) · standard (one agentic act) ·
# deep (act + grounding) · steward (admin). See docs/concepts/15.
SCN = [
    {"id": "relational-hi", "flow": "relational", "tier": "direct", "q": "hello there!",
     "path": "relational", "seq": ["relational:synthesize"], "grounded": False},
    {"id": "relational-thanks", "flow": "relational", "tier": "direct", "q": "thanks, that's great",
     "path": "relational", "seq": ["relational:synthesize"], "grounded": False},
    {"id": "qna-fact", "flow": "qna", "tier": "standard", "q": "what is the capital of France?",
     "path": "qna", "seq": ["qna:act"], "grounded": False},
    {"id": "qna-howto", "flow": "qna", "tier": "standard", "q": "how does TCP congestion control work?",
     "path": "qna", "seq": ["qna:act"], "grounded": False},
    {"id": "clarify-followup", "flow": "clarify", "tier": "standard", "q": "what about that one?",
     "path": "clarify", "seq": ["clarify:synthesize"], "grounded": False,
     "warmup": "what is the PTO policy?"},
    {"id": "research-compare", "flow": "research", "tier": "deep",
     "q": "compare React and Vue in depth and cite sources", "path": "research",
     "seq": ["research:act", "research:cite", "research:filter"], "grounded": True},
    {"id": "research-tradeoffs", "flow": "research", "tier": "deep",
     "q": "research the tradeoffs of microservices vs monolith across cost, scale and ops",
     "path": "research", "seq": ["research:act", "research:cite", "research:filter"],
     "grounded": True},
    {"id": "fallback-nonsense", "flow": "fallback", "tier": "standard",
     "q": "xyzzy plugh frobnicate the borogoves",
     "path": "emergent", "seq": ["fallback:act"], "grounded": False},
    {"id": "onboarding-steward", "flow": "onboarding", "tier": "steward",
     "q": "set up the knowledge base for the team",
     "path": "onboarding", "seq": ["onboarding:synthesize"], "grounded": False,
     "config_mode": True},
    # adversarial near-neighbours — routing must not be fooled.
    {"id": "adv-greeting-question", "flow": "qna", "tier": "standard",
     "q": "hi! quick one — what is the capital of Japan?", "path": "qna",
     "seq": ["qna:act"], "grounded": False, "adversarial": True},
    {"id": "adv-imperative", "flow": "fallback", "tier": "standard",
     "q": "summarize the theory of general relativity for me", "path": "emergent",
     "seq": ["fallback:act"], "grounded": False, "adversarial": True},
]


def _ck(cid, ok, detail):
    return {"id": cid, "ok": bool(ok), "detail": detail}


def _payload(checks, metrics=None):
    return {"checks": checks, "n": len(checks), "pass": sum(c["ok"] for c in checks),
            "all_pass": all(c["ok"] for c in checks) and bool(checks), "metrics": metrics or {}}


def _agent(scn: dict) -> PreactAgent:
    ctx = {"config_mode": True} if scn.get("config_mode") else None
    return PreactAgent(client=FakeClient(["ok"] * 8), instructions="You are a helpful assistant.",
                       context=ctx)


def _state(scn: dict) -> SessionState:
    """Seed the history a flow needs to route (clarify's anaphora needs a prior info turn)."""
    if scn.get("warmup"):
        return SessionState(history=[Turn("user", scn["warmup"]), Turn("assistant", "It is 20 days.")])
    return SessionState()


def _snap(scn: dict):
    return _agent(scn).engine.inspect(scn["q"], _state(scn))


# ── routing: query → right path + flow-qualified state sequence ──────────────────────────────────
def run_routing() -> dict:
    checks = []
    for s in SCN:
        snap = _snap(s)
        ok = snap.path[0] == s["path"] and list(snap.flow) == s["seq"]
        checks.append(_ck(f"routing.{s['id']}", ok, f"path={snap.path[0]} flow={list(snap.flow)}"))
    return _payload(checks)


# ── states: canonical vocabulary + canonical order ──────────────────────────────────────────────
def run_states() -> dict:
    checks = []
    for s in SCN:
        states = [q.split(":", 1)[-1] for q in s["seq"]]
        in_vocab = all(st in _ORDER for st in states)
        ordered = all(_ORDER[states[i]] <= _ORDER[states[i + 1]] for i in range(len(states) - 1))
        checks.append(_ck(f"states.{s['id']}", in_vocab and ordered,
                          f"states={states} vocab={in_vocab} ordered={ordered}"))
    return _payload(checks, {"canonical": list(_CANONICAL)})


# ── grounding: deep grounds (cite→filter); social/standard do not ────────────────────────────────
def run_grounding() -> dict:
    checks = []
    for s in SCN:
        states = [q.split(":", 1)[-1] for q in s["seq"]]
        has_ground = states[-2:] == ["cite", "filter"]
        ok = has_ground if s["grounded"] else ("cite" not in states and "filter" not in states)
        checks.append(_ck(f"grounding.{s['id']}", ok,
                          f"grounded={s['grounded']} tail={states[-2:]}"))
    return _payload(checks)


# ── tiers: the complexity grouping (direct/standard/deep/steward) + its grounding contract ───────
_TIERS = {"direct", "standard", "deep", "steward"}


def run_tiers() -> dict:
    """Each flow maps to a complexity tier, and the tier fixes the grounding contract: only ``deep``
    carries the pinned ``cite → filter`` tail; ``direct``/``standard``/``steward`` do not. Also
    asserts every tier in the simple→complex spectrum is represented."""
    checks = []
    for s in SCN:
        tier = s["tier"]
        states = [q.split(":", 1)[-1] for q in s["seq"]]
        grounded = states[-2:] == ["cite", "filter"]
        ok = tier in _TIERS and (grounded if tier == "deep" else not grounded)
        checks.append(_ck(f"tiers.{s['id']}", ok, f"tier={tier} grounded={grounded}"))
    covered = {s["tier"] for s in SCN}
    checks.append(_ck("tiers.spectrum_covered", covered >= _TIERS, f"covered={sorted(covered)}"))
    return _payload(checks, {"tiers": sorted(covered)})


# ── coverage: every default flow is exercised ────────────────────────────────────────────────────
def run_coverage() -> dict:
    agent = PreactAgent(client=FakeClient(["ok"]), instructions="x")
    defined = {f.id for f in agent.engine.flows}
    covered = {s["flow"] for s in SCN}
    missing = sorted(defined - covered)
    checks = [_ck("coverage.all_flows_tested", not missing,
                  f"defined={sorted(defined)} untested={missing}")]
    return _payload(checks, {"flows_defined": len(defined), "flows_covered": len(covered & defined)})


# ── determinism: routing is a pure function of (spec, context) ───────────────────────────────────
def run_determinism() -> dict:
    checks = []
    for s in SCN[:4]:
        a, b = _snap(s), _snap(s)
        ok = tuple(a.path) == tuple(b.path) and list(a.flow) == list(b.flow)
        checks.append(_ck(f"determinism.{s['id']}", ok, "identical across two inspects"))
    return _payload(checks)


# ── subject: a state instantiated against a subject threads it into the prompt ────────────────────
def run_subject() -> dict:
    from agent_sdk.session import SessionState as _SS
    from agent_sdk.stages import stage

    agent = PreactAgent(client=FakeClient(["ok"]), instructions="x")
    st = stage("act", lobes=["synthesize"], loop="agentic", subject="aspect-3: licensing differences")
    sysp = agent.engine._compose_system(st, {"query": "x"}, _SS(), [], is_last=True)
    checks = [
        _ck("subject.threaded", "aspect-3: licensing differences" in sysp, "subject text in prompt"),
        _ck("subject.tagged", "<subject>" in sysp, "subject rendered as its own <subject> section"),
    ]
    return _payload(checks)


# ── execution: each flow actually RUNS its declared stages and answers ───────────────────────────
async def run_execution() -> dict:
    checks = []
    for s in SCN:
        agent = PreactAgent(client=FakeClient(["ok"] * 8), instructions="You are a helpful assistant.",
                            context=({"config_mode": True} if s.get("config_mode") else None),
                            session=Session(s["id"], SessionStoreInMemory()))
        if s.get("warmup"):
            await probe(agent, s["warmup"], label=f"{s['id']}·warmup")
        rec = await probe(agent, s["q"], label=s["id"])
        ran = [st.get("stage") for st in rec.stages]
        ok = ran == s["seq"] and rec.status in ("answered", "refused")
        checks.append(_ck(f"execution.{s['id']}", ok, f"ran={ran} status={rec.status}"))
    return _payload(checks)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--label", default="base")
    ap.add_argument("--live", action="store_true")          # accepted for ladder uniformity
    ap.add_argument("--model", default=None)
    ap.add_argument("--trials", type=int, default=1)
    args = ap.parse_args()

    payloads = {
        "routing": run_routing(),
        "tiers": run_tiers(),
        "states": run_states(),
        "grounding": run_grounding(),
        "coverage": run_coverage(),
        "determinism": run_determinism(),
        "subject": run_subject(),
        "execution": await run_execution(),
    }
    verdict = compose_verdict(payloads, record={"coverage": ["flows_defined", "flows_covered"],
                                                "tiers": ["tiers"]})

    print("── flowbench ──────────────────────────────────────────────────")
    total = ok = 0
    for p in payloads.values():
        for c in p["checks"]:
            print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['id']:<28} {c['detail'][:46]}")
        total += p["n"]
        ok += p["pass"]
    print(f"\nflowbench: {ok}/{total} checks pass · verdict {verdict['status']}")
    if verdict["metrics"]:
        print("metrics:", verdict["metrics"])
    if verdict["reasons"]:
        print("reasons:", "; ".join(verdict["reasons"]))

    if args.report:
        RESULTS.mkdir(exist_ok=True)
        write_viewer(RESULTS / "flowbench.html", [], label="flowbench · flow axis (OX)",
                     verdict=verdict, modes=payloads)
        html, md = emit_report(HERE, "flowbench", label="flowbench · flow axis (OX)",
                               verdict=verdict, modes=payloads)
        print(f"report: {RESULTS / 'flowbench.html'}\ncommitted: {md} · {html}")
    return 0 if verdict["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
