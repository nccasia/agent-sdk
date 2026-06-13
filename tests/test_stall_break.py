"""P1 — the opt-in no-progress / repetition stall-break in the agentic loop.

A run that repeats the same (name, args) tool call without making progress should,
once `stall_patience` consecutive no-progress hops accrue, be steered to converge
and forced tool-free so it must answer — instead of looping to the hop ceiling.
Unset ⇒ no-op (byte-identical to the unbounded loop).
"""

from __future__ import annotations

from agent_sdk import PreactAgent, flow, probe, stage, tool


def _repeating_handler():
    """Emits the same tool call every hop, but answers when handed no tools."""

    def handler(stage_id, system, messages, tools):
        if not tools:  # forced-final hop (the loop withheld tools)
            return "final answer from what I have"
        return {"tools": [{"name": "noop", "input": {"x": 1}}]}

    return handler


def _build(patience: int | None):
    @tool
    async def noop(x: int) -> str:
        return f"noop {x}"

    from agent_sdk.clients.fake import scripted

    budgets = {"stall_patience": patience} if patience is not None else None
    return noop, PreactAgent(
        client=scripted(_repeating_handler()),
        instructions="bot",
        tools=[noop],
        budgets=budgets,
        flows=[flow("qna", stages=["work"], signal={"const": 1.0})],
        stages=[stage("work", lobes=["synthesize"], loop="agentic", tools=["noop"], hops=40)],
    )


async def test_stall_break_forces_early_finish():
    _, agent = _build(patience=2)
    rec = await probe(agent, "go", label="t")
    assert rec.status == "answered"
    assert any(m.get("action") == "stall_break" for m in rec.meta_actions)
    # break fired ~ patience hops in, well under the 40-hop ceiling
    assert len(rec.llm_calls) <= 6
    assert "final answer" in rec.answer


async def test_no_stall_patience_runs_to_ceiling():
    _, agent = _build(patience=None)  # default: knob unset → no stall-break
    rec = await probe(agent, "go", label="t")
    assert not any(m.get("action") == "stall_break" for m in rec.meta_actions)
    # without the break, it loops the full budget (last hop withholds tools → answers)
    assert len(rec.llm_calls) >= 40


async def test_progress_resets_the_stall_counter():
    @tool
    async def noop(x: int) -> str:
        return f"noop {x}"

    # each hop makes a *new* call (distinct input) → never stalls → no break
    from agent_sdk.clients import FakeClient

    script = [{"tools": [{"name": "noop", "input": {"x": i}}]} for i in range(8)] + ["done"]
    agent = PreactAgent(
        client=FakeClient(script),
        instructions="bot",
        tools=[noop],
        budgets={"stall_patience": 2},
        flows=[flow("qna", stages=["work"], signal={"const": 1.0})],
        stages=[stage("work", lobes=["synthesize"], loop="agentic", tools=["noop"], hops=40)],
    )
    rec = await probe(agent, "go", label="t")
    assert not any(m.get("action") == "stall_break" for m in rec.meta_actions)
    assert rec.answer == "done"
