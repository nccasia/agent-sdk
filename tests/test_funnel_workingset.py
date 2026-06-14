"""Phase 2 — bound the loop: value-aware pinning + budget-driven compaction.

``tier_observations`` alone bounds the growth *rate* but the spent-hint tail is
still O(hops), and recency alone silently demotes an old-but-critical
observation. These tests prove the working-set discipline: value (CDS vs the
goal) beats recency, errors stay full, the tail stays bounded under a budget,
and the default (no budget) path is unchanged.
"""

from __future__ import annotations

from agent_sdk import PreactAgent, flow, probe, stage, tool
from agent_sdk.clients import FakeClient
from agent_sdk.react.funnel import (
    compact_observations,
    obs_tail_tokens,
    score_observations,
    tier_observations,
)


def _obs(
    tid: str, text: str, *, name: str = "f", inp: dict | None = None, is_error: bool = False
) -> list[dict]:
    """One think→act→observe exchange (assistant tool_use + user tool_result)."""
    tr = {"type": "tool_result", "tool_use_id": tid, "content": text}
    if is_error:
        tr["is_error"] = True
    return [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp or {}}],
        },
        {"role": "user", "content": [tr]},
    ]


def test_score_observations_value_beats_recency():
    """An old on-goal observation outranks a newer off-goal one."""
    msgs = _obs(
        "t1",
        "deployment release production rollout steps are prepared",
        name="search",
        inp={"q": "deploy release production"},
    ) + _obs(
        "t2",
        "sunny twenty five degrees clear sky weather forecast today",
        name="weather",
        inp={"q": "weather"},
    )
    keep = score_observations(msgs, goal="deploy the release to production", keep_top=1)
    assert keep == {"t1"}  # value (on-goal) beat recency (the newer weather obs)


def test_score_observations_keep_top_zero():
    msgs = _obs("t1", "anything")
    assert score_observations(msgs, goal="x", keep_top=0) == set()


def test_pinned_observation_stays_full_against_recency():
    """A value-pinned old observation survives even as newer ones arrive."""
    msgs = (
        _obs("old", "CRITICAL the database password rotates at midnight UTC")
        + _obs("a", "x" * 50)
        + _obs("b", "y" * 50)
        + _obs("c", "z" * 50)
    )
    out = tier_observations(msgs, hop=4, keep_last_full=1, keep_full_ids={"old"})
    # the pinned observation keeps its full body; un-pinned older ones are hints
    old_tr = next(
        b
        for m in out
        for b in m.get("content", [])
        if isinstance(b, dict) and b.get("tool_use_id") == "old"
    )
    assert "CRITICAL the database password" in old_tr["content"]


def test_error_observation_stays_full():
    msgs = (
        _obs("err", "Traceback: ConnectionRefused on port 5432", is_error=True)
        + _obs("a", "x" * 80)
        + _obs("b", "y" * 80)
    )
    out = tier_observations(msgs, hop=3, keep_last_full=1, keep_errors_full=True)
    err_tr = next(
        b
        for m in out
        for b in m.get("content", [])
        if isinstance(b, dict) and b.get("tool_use_id") == "err"
    )
    assert "Traceback" in err_tr["content"]  # error body survived the funnel


def test_budget_compaction_bounds_tail_vs_recency_only():
    """Over many hops, recency-only grows; budget-driven compaction plateaus."""
    msgs_r: list[dict] = []
    msgs_b: list[dict] = []
    for i in range(40):
        chunk_r = _obs(f"t{i}", f"observation number {i} with some detail " * 8)
        chunk_b = _obs(f"t{i}", f"observation number {i} with some detail " * 8)
        msgs_r += chunk_r
        msgs_b += chunk_b
        msgs_r[:] = tier_observations(msgs_r, hop=i, keep_last_full=2)
        msgs_b[:] = tier_observations(msgs_b, hop=i, keep_last_full=2)
        if obs_tail_tokens(msgs_b) > 150:
            msgs_b[:] = compact_observations(msgs_b, keep_last_full=2, max_spent=6)
    recency_tail = obs_tail_tokens(msgs_r)
    budgeted_tail = obs_tail_tokens(msgs_b)
    assert budgeted_tail < recency_tail  # the budget bounds what recency lets grow
    assert budgeted_tail <= 600  # and keeps it near the working-set budget


# ── engine-level: the budget bounds the live funnel tail (Phase 1 telemetry) ──
def _looping_agent(budget: int | None):
    @tool
    async def big(q: str) -> str:
        return f"chunk {q}: " + ("lorem ipsum dolor sit amet " * 24)

    budgets = {"working_set_budget": budget, "working_set_keep": 2} if budget else {}
    return PreactAgent(
        client=FakeClient(
            [{"tools": [{"name": "big", "input": {"q": str(i)}}]} for i in range(40)] + ["done"]
        ),
        instructions="bot",
        tools=[big],
        flows=[flow("qna", stages=["work"], signal={"const": 1.0})],
        stages=[stage("work", lobes=["synthesize"], loop="agentic", tools=["big"], hops=50)],
        funnel=True,
        budgets=budgets,
    )


async def test_working_set_budget_bounds_engine_tail():
    bounded = await probe(_looping_agent(budget=120), "explore everything", label="b")
    default = await probe(_looping_agent(budget=None), "explore everything", label="d")

    def peak(rec):
        series = [
            c for s in rec.stages for c in (s.get("metadata") or {}).get("funnel_obs_chars", [])
        ]
        return max(series) if series else 0

    assert peak(bounded) > 0 and peak(default) > 0
    assert peak(bounded) < peak(default)  # the budget plateaus the tail


async def test_default_funnel_path_unchanged_without_budget():
    """No working_set_budget → the recency-only path still runs and answers."""
    rec = await probe(_looping_agent(budget=None), "explore everything", label="d")
    assert rec.status == "answered"
    # the funnel still ran (telemetry recorded a per-hop tail series)
    series = [c for s in rec.stages for c in (s.get("metadata") or {}).get("funnel_obs_chars", [])]
    assert series and series[-1] > 0
