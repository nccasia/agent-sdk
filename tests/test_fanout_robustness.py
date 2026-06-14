"""Fan-out robustness — a wide parallel fan-out must not collapse silently.

Regression for the observed bug: ≥7 simultaneous subagent calls trip the provider's
concurrency limit, every worker errors, and bounded-failure drops them all (subagents=0)
while fan-in still answers — masking the failure. The engine now (1) caps concurrency,
(2) guards the WHOLE worker body so a failure is recorded (never silently dropped),
(3) retries a failed worker once serially, and (4) leaves a `degraded` marker if it still
fails. Driven offline through the planning fan-out with a flaky scripted client.
"""

from __future__ import annotations

from agent_sdk import PreactAgent, probe
from agent_sdk.clients.fake import scripted
from agent_sdk.memory.scratchpad import Scratchpad
from agent_sdk.plugins.planning import PlanningPlugin


def test_scratchpad_cap_preserves_list_type_for_wide_fanout():
    """The real wide-fan-out collapse: a results list whose JSON exceeds the value cap must STAY a
    list (consumers read it via as_list + isinstance(dict)). It must never become a sentinel dict
    (which read as zero rows and sank wide fan-outs)."""
    sp = Scratchpad()
    sp.set("todos_results", [{"label": f"t{i}", "result": "x" * 1500, "status": "ok"} for i in range(8)])
    assert isinstance(sp.get("todos_results"), list)  # never collapsed to a dict
    rows = [r for r in sp.as_list("todos_results") if isinstance(r, dict) and r.get("status")]
    assert len(rows) >= 1  # real result rows survive (not zero)


def _planner(seen: dict, *, fail_times: int):
    """A model that plans 6 independent designed todos (→ fanout), and whose execute workers
    raise on their first ``fail_times`` calls (simulating a concurrency-limit rejection),
    succeeding afterwards."""

    def model(sid, sy, m, t):
        last = str(m[-1]["content"]) if m else ""
        if sid == "plan":
            if "Todos updated" in last:
                return "planned"
            todos = [
                {"content": f"do {n}", "status": "pending", "prompt": f"handle {n}"}
                for n in ("a", "b", "c", "d", "e", "f")
            ]
            return {"tools": [{"name": "TodoWrite", "input": {"todos": todos}}]}
        if sid == "execute":
            seen[last] = seen.get(last, 0) + 1
            if seen[last] <= fail_times:
                raise RuntimeError("rate limited (concurrent burst)")
            return f"result for {last.rsplit(' ', 1)[-1]}"
        return "combined answer"

    return model


_Q = "Research six independent topics — a, b, c, d, e, and f — and report each."


async def test_burst_failures_recover_via_serial_retry():
    seen: dict = {}
    agent = PreactAgent(
        client=scripted(_planner(seen, fail_times=1)),  # fail once per worker → retry recovers
        instructions="bot",
        plugins=[PlanningPlugin()],
    )
    rec = await probe(agent, _Q, label="t")
    assert rec.blackboard.get("plan_structure") == "fanout"
    results = rec.blackboard.get("todos_results", [])
    # all six workers recorded (never silently dropped) and recovered to ok by the serial retry
    assert len(results) == 6
    assert all(r["status"] == "ok" for r in results)
    assert not rec.degraded  # full recovery ⇒ no degraded marker
    assert rec.status == "answered"


async def test_persistent_failure_is_recorded_and_marked_degraded():
    seen: dict = {}
    agent = PreactAgent(
        client=scripted(_planner(seen, fail_times=99)),  # never succeeds, even on retry
        instructions="bot",
        plugins=[PlanningPlugin()],
    )
    rec = await probe(agent, _Q, label="t")
    results = rec.blackboard.get("todos_results", [])
    # workers are RECORDED as failed (not silently dropped → subagents=0), and the turn is
    # flagged degraded so a collapse is visible rather than masked by fan-in.
    assert len(results) == 6
    assert all(r["status"] == "failed" for r in results)
    assert any(d.startswith("fanout:") for d in rec.degraded)
