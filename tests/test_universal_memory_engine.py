"""Universal memory wired into a live turn — the funnel offloads spent tool bodies into the
store, and the model can read them back (recall) and write reasoning state (note)."""

from __future__ import annotations

from agent_sdk import PreactAgent, flow, probe, stage, tool
from agent_sdk.clients import FakeClient


def _looping_agent(handler, *, universal_memory: bool):
    @tool
    async def big(q: str) -> str:
        return f"OBSERVATION {q}: needle_{q} at src/mod{q}.py " + ("lorem ipsum detail " * 20)

    return PreactAgent(
        client=FakeClient([handler] * 200),
        instructions="bot",
        tools=[big],
        flows=[flow("qna", stages=["work"], signal={"const": 1.0})],
        stages=[stage("work", lobes=["synthesize"], loop="agentic", tools=["big"], hops=30)],
        funnel=True,
        universal_memory=universal_memory,
        budgets={"working_set_budget": 120, "working_set_keep": 2},
    )


class _Driver:
    """Calls the tool many times (forcing compaction → offload), then reads a body back, then
    writes a decision, then answers."""

    def __init__(self) -> None:
        self.n = 0

    def __call__(self, stage, system, messages, tools):
        self.n += 1
        if self.n <= 12:
            return {"tools": [{"name": "big", "input": {"q": str(self.n)}}]}
        if self.n == 13:
            return {"tools": [{"name": "recall", "input": {"query": "needle_2"}}]}
        if self.n == 14:
            return {
                "tools": [
                    {
                        "name": "note",
                        "input": {"content": "needle_2 is the root cause", "kind": "decision"},
                    }
                ]
            }
        return "Done: found needle_2 and recorded the decision."


async def test_funnel_offloads_into_store_and_model_reads_back():
    driver = _Driver()
    agent = _looping_agent(driver, universal_memory=True)
    rec = await probe(agent, "investigate the incident", label="universal")

    assert rec.status == "answered"
    # the funnel offloaded spent tool bodies into flash memory during compaction
    store = agent._memory_store
    assert store is not None and store.stats()["flash"] > 0
    # an early, compacted observation is re-fetchable from the store
    early = store.recall(query="needle_2")
    assert early and any("needle_2" in store.read(e.handle) for e in early)
    # the model called recall + note in the live loop (essentials, exposed despite the allowlist)
    called = {c["name"] for c in rec.tool_calls}
    assert "recall" in called and "note" in called
    # the note (a decision) is in memory
    decisions = store.recall(kind="decision")
    assert decisions and "root cause" in decisions[0].body


async def test_default_has_no_universal_memory_tools():
    """Off by default — no recall/note tool, no store (byte-identical surface)."""
    agent = _looping_agent(_Driver(), universal_memory=False)
    assert agent._memory_store is None
    specs = {s["name"] for s in agent.engine.tools.get_tool_specs()}
    assert "recall" not in specs and "note" not in specs
