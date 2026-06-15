#!/usr/bin/env python3
"""statelessbench — proves the SDK runs STATELESS: snapshot/restore + concurrent multi-session serving.

The stateless contract (docs/concepts): a process holds only the (immutable) agent config; ALL
per-session state — conversation AND universal working memory — rides in a plain-JSON snapshot
(``SessionState.to_json``). So any worker/replica serves any session, a restart loses nothing, and
one shared agent (or an ``AgentWorker`` pool) serves thousands of sessions without cross-contamination.

Fully DETERMINISTIC (FakeClient): this measures the snapshot/restore/serve plumbing, not LLM quality —
so it's a free gate. Five modes, each a `compose_verdict` payload:

  snapshot   — run_snapshot(input, snap)→(result, snap): a fact stored on turn 1 survives a hop to a
               FRESH agent on turn 2 (the easy stateless API).
  store      — any SessionStore (here SQLite) round-trips the WHOLE state (history + memory) across a
               new agent + new Session on the same id.
  isolation  — AgentWorker(agent_factory=…) pool runs N distinct sessions concurrently; each session's
               memory holds ONLY its own facts (no cross-session bleed) — the concurrency proof.
  spec       — agent.spec().to_json()→from_json rebuilds an identical config (the "init from JSON" half).
  schema     — the snapshot schema is versioned + tolerant (old/unknown keys load) → extensible later.

    python benchmarks/statelessbench/run.py --report --label base
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # packages/agent-sdk on path
from agent_sdk import PreactAgent  # noqa: E402
from agent_sdk.clients import FakeClient  # noqa: E402
from agent_sdk.serve import AgentWorker, InProcessEventSink, InProcessQueue, Job  # noqa: E402
from agent_sdk.session import SNAPSHOT_VERSION, Session, SessionState  # noqa: E402
from agent_sdk.spec import PreactSpec, agent_from_spec  # noqa: E402
from agent_sdk.stores.session import SessionStoreSQL  # noqa: E402
from benchmarks._shared import compose_verdict  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

# A turn that hands the agent fact-bearing bullets — auto-establish stores them deterministically
# (no LLM needed), so memory content is a pure function of the input.
_ANSWER = "Noted."


def _agent() -> PreactAgent:
    # universal_memory on = the working-memory store under test; ample FakeClient script for any hops.
    return PreactAgent(client=FakeClient([_ANSWER] * 8), instructions="You are helpful.")


def _facts_in(snapshot: dict) -> str:
    """All long-term memory bodies in a snapshot, lowered — for substring assertions."""
    long = ((snapshot or {}).get("memory") or {}).get("long") or []
    return " ".join(str(e.get("body", "")) for e in long).lower()


def _check(checks: list, cid: str, ok: bool, detail: str) -> None:
    checks.append({"id": cid, "ok": bool(ok), "detail": detail})


def _payload(checks: list, **metrics) -> dict:
    n = len(checks)
    return {
        "checks": checks,
        "n": n,
        "pass": sum(c["ok"] for c in checks),
        "all_pass": all(c["ok"] for c in checks) and n > 0,
        "metrics": metrics,
    }


async def run_snapshot() -> dict:
    """The easy stateless API: a fact stored on turn 1 survives a hop to a FRESH agent on turn 2."""
    checks: list = []
    a1 = _agent()
    _r1, snap1 = await a1.run_snapshot("Remember:\n- My name is Alice\n- I work at Acme Corp")
    _check(checks, "snapshot.fact_stored", "alice" in _facts_in(snap1),
           "turn-1 fact captured into the snapshot's memory")
    _check(checks, "snapshot.history_grew", len(snap1.get("history", [])) == 2,
           "user+assistant turn recorded")
    _check(checks, "snapshot.versioned", snap1.get("v") == SNAPSHOT_VERSION,
           f"snapshot stamps v={SNAPSHOT_VERSION}")

    # A DIFFERENT agent instance (simulates a new process / replica) continues from the snapshot.
    a2 = _agent()
    _r2, snap2 = await a2.run_snapshot("What is my name?", snapshot=snap1)
    _check(checks, "snapshot.memory_survived_hop", "alice" in _facts_in(snap2),
           "the fact restored into a fresh agent and persisted forward")
    _check(checks, "snapshot.history_carried", len(snap2.get("history", [])) == 4,
           "prior turns carried across the stateless hop")
    return _payload(checks, modes=1)


async def run_store() -> dict:
    """Any SessionStore round-trips the WHOLE state (history + universal memory), not just turns."""
    checks: list = []
    store = SessionStoreSQL(":memory:")
    sid = "conv-1"

    a1 = _agent()
    await a1.query("Remember:\n- The deploy window is Thursday 14:00 UTC",
                   session=Session(sid, store))
    persisted = await store.load(sid)
    _check(checks, "store.memory_persisted", "deploy window" in _facts_in(persisted.to_json()),
           "universal memory saved into the store (not just the transcript)")
    _check(checks, "store.history_persisted", len(persisted.history) == 2, "turns saved")

    # A fresh agent + fresh Session on the SAME store/id resumes with full state.
    a2 = _agent()
    await a2.query("Anything else?", session=Session(sid, store))
    resumed = await store.load(sid)
    _check(checks, "store.resumes_across_agents", "deploy window" in _facts_in(resumed.to_json()),
           "memory still present after a different agent continued the session")
    _check(checks, "store.history_continued", len(resumed.history) == 4, "history continued")
    return _payload(checks, modes=1)


async def run_isolation() -> dict:
    """The concurrency proof: a pooled worker runs N distinct sessions; no memory bleeds across them."""
    checks: list = []
    store = SessionStoreSQL(":memory:")
    people = [("alice", "acme"), ("bob", "bytecorp"), ("carol", "cogni"),
              ("dave", "dynamo"), ("erin", "edgeware"), ("frank", "fathom")]
    worker = AgentWorker(
        agent_factory=_agent,  # a POOL of agents — each turn checks one out exclusively
        queue=InProcessQueue(),
        sink=InProcessEventSink(),
        concurrency=4,  # < number of sessions, so agents are REUSED across sessions (the bleed risk)
    )
    jobs = [
        Job(input=f"Remember:\n- My name is {name}\n- I work at {org}",
            session=Session(f"sess-{name}", store), trace_id=f"t-{name}")
        for name, org in people
    ]
    for j in jobs:
        await worker.queue.enqueue(j)
    await worker.serve(max_jobs=len(jobs))

    clean = 0
    for name, org in people:
        facts = _facts_in((await store.load(f"sess-{name}")).to_json())
        own = name in facts and org in facts
        others = [p for p, _ in people if p != name and p in facts]
        ok = own and not others
        clean += ok
        _check(checks, f"isolation.{name}_only_own", ok,
               f"own facts present, no bleed (stray: {others})" if not ok else "isolated")
    return _payload(checks, sessions=len(people), pool=4, clean=clean)


async def run_worker() -> dict:
    """The effective-queue loop: jobs carry ONLY a session_id; the worker binds it to ONE store and
    runs load→turn→offload natively — including resume-by-id on a later turn."""
    checks: list = []
    store = SessionStoreSQL(":memory:")
    worker = AgentWorker(
        agent_factory=_agent, queue=InProcessQueue(), sink=InProcessEventSink(),
        store=store, concurrency=3,
    )
    # Turn 1 for three sessions — the caller passes NO Session object, only an id.
    for who in ("amy", "ben", "cy"):
        await worker.queue.enqueue(Job(input=f"Remember:\n- My name is {who}", session_id=f"s-{who}"))
    await worker.serve(max_jobs=3)
    bound = True
    for who in ("amy", "ben", "cy"):
        bound = bound and who in _facts_in((await store.load(f"s-{who}")).to_json())
    _check(checks, "worker.binds_id_to_store", bound,
           "each job's session_id was loaded/saved against the one store")

    # Turn 2 for one session — resumed purely from its id (the snapshot JSON carries everything).
    await worker.queue.enqueue(Job(input="Remember:\n- I work at Acme", session_id="s-amy"))
    await worker.serve(max_jobs=1)
    resumed = (await store.load("s-amy")).to_json()
    _check(checks, "worker.resumes_by_id", "amy" in _facts_in(resumed) and "acme" in _facts_in(resumed),
           "turn-2 resumed the session from its id and merged new memory")
    _check(checks, "worker.history_accrued", len(resumed.get("history", [])) == 4,
           "both turns recorded in the one JSON")
    return _payload(checks, sessions=3)


def run_spec() -> dict:
    """Config (the immutable half) is JSON-portable: spec.to_json → from_json → identical agent."""
    checks: list = []
    a = PreactAgent(client=FakeClient([_ANSWER]), instructions="You are a concise assistant.",
                    require_citations=False)
    j = a.spec().to_json()
    rebuilt = agent_from_spec(PreactSpec.from_json(j), client=FakeClient([_ANSWER]))
    _check(checks, "spec.roundtrips", rebuilt.spec().to_json() == j,
           "agent rebuilt from JSON spec is byte-identical")
    _check(checks, "spec.has_network", bool(j.get("lobes")) and bool(j.get("flows")),
           "spec captures the network (lobes/flows)")
    return _payload(checks, modes=1)


def run_schema() -> dict:
    """The snapshot schema is versioned + tolerant → safe to extend later (no migration needed)."""
    checks: list = []
    full = SessionState(summary="hi", skills_in_use=["x"], meta_flow_bias="research",
                        memory={"seq": 1, "long": [], "docs": {}}).to_json()
    _check(checks, "schema.versioned", full.get("v") == SNAPSHOT_VERSION, "carries a version stamp")
    _check(checks, "schema.carries_memory", "memory" in full, "memory rides the snapshot")

    # forward-compat: an UNKNOWN future key loads without error and is ignored.
    fwd = dict(full, _future_field={"anything": True})
    st = SessionState.from_json(fwd)
    _check(checks, "schema.tolerates_unknown", st.skills_in_use == ["x"],
           "unknown keys ignored, known state intact")
    # backward-compat: an OLD snapshot (no memory / no v) loads with safe defaults.
    old = {"history": [], "summary": "legacy"}
    st2 = SessionState.from_json(old)
    _check(checks, "schema.tolerates_missing", st2.summary == "legacy" and st2.memory == {},
           "missing keys default; no crash")
    return _payload(checks, version=SNAPSHOT_VERSION)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true", help="write results/<label>.json")
    ap.add_argument("--label", default="base")
    a = ap.parse_args()

    payloads: dict[str, dict | None] = {
        "snapshot": asyncio.run(run_snapshot()),
        "store": asyncio.run(run_store()),
        "worker": asyncio.run(run_worker()),
        "isolation": asyncio.run(run_isolation()),
        "spec": run_spec(),
        "schema": run_schema(),
    }
    verdict = compose_verdict(
        payloads,
        record={"isolation": ["clean", "sessions"], "worker": ["sessions"]},
    )

    total = sum(p["n"] for p in payloads.values() if p)
    passed = sum(p["pass"] for p in payloads.values() if p)
    for p in payloads.values():
        if p:
            for c in p["checks"]:
                if not c["ok"]:
                    print(f"  FAIL {c['id']}: {c['detail']}", file=sys.stderr)
    print(f"{passed}/{total} checks pass · verdict {verdict['status']}")
    if verdict["reasons"]:
        print("  " + "; ".join(verdict["reasons"]))

    if a.report:
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / f"{a.label}.json").write_text(
            json.dumps({"verdict": verdict, "payloads": payloads}, indent=2, ensure_ascii=False)
        )

    return 0 if verdict["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
