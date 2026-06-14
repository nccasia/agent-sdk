"""The finalize + tool-result hook seams — a plugin's grounding/citation contract.

The engine carries no citation logic of its own; a plugin (RagPlugin) owns it via
``add_finalize_hook`` (rewrite the answer / replace citations / force a refusal in
``_finalize``) and ``add_tool_result_hook`` (extract citations a tool emits). These
lock the seam in: hooks run, can mutate the result, and the default (no hook) path
is unchanged.
"""

from __future__ import annotations

from agent_sdk import PreactAgent
from agent_sdk.clients.fake import FakeClient
from agent_sdk.contracts.memo import Citation


class _FinalizePlugin:
    name = "finalize_probe"

    def __init__(self, *, rewrite=None, add_citation=None, refuse=False):
        self.rewrite = rewrite
        self.add_citation = add_citation
        self.refuse = refuse
        self.seen: list[tuple] = []

    def install(self, setup):
        def hook(answer, citations, chunks, grounds, require_citations):
            self.seen.append((answer, list(citations), grounds, require_citations))
            new_cites = list(citations)
            if self.add_citation is not None:
                new_cites.append(self.add_citation)
            return (
                self.rewrite if self.rewrite is not None else answer,
                new_cites,
                "policy_violation" if self.refuse else None,
            )

        setup.add_finalize_hook(hook)


def _agent(plugins=None) -> PreactAgent:
    return PreactAgent(client=FakeClient(default="hello world"), plugins=plugins or [])


async def test_finalize_hook_runs_and_sees_the_answer():
    p = _FinalizePlugin()
    agent = _agent([p])
    await agent.query("hi")
    assert p.seen, "finalize hook was never called"
    assert p.seen[0][0] == "hello world"


async def test_finalize_hook_rewrites_the_answer():
    agent = _agent([_FinalizePlugin(rewrite="REWRITTEN")])
    res = await agent.query("hi")
    assert res.text == "REWRITTEN"
    assert res.status == "answered"


async def test_finalize_hook_can_add_a_citation():
    cit = Citation(chunk_id="c1", source_ref="doc://x", supporting_span=(0, 5))
    agent = _agent([_FinalizePlugin(add_citation=cit)])
    res = await agent.query("hi")
    assert any(c.chunk_id == "c1" for c in res.citations)


async def test_finalize_hook_can_force_a_refusal():
    agent = _agent([_FinalizePlugin(refuse=True)])
    res = await agent.query("hi")
    assert res.status == "refused"
    assert res.refusal is not None
    assert res.refusal.reason == "policy_violation"


async def test_no_finalize_hook_leaves_the_turn_unchanged():
    res = await _agent([]).query("hi")
    assert res.status == "answered"
    assert res.text == "hello world"


class _ToolResultPlugin:
    name = "tool_result_probe"

    def install(self, setup):
        def hook(tool_name, output):
            return [Citation(chunk_id="from-tool", source_ref="t://1", supporting_span=(0, 1))]

        setup.add_tool_result_hook(hook)


async def test_tool_result_hook_seam_registers():
    # The seam wires through to the engine even when no tools run this turn.
    agent = _agent([_ToolResultPlugin()])
    assert len(agent.engine._tool_result_hooks) == 1
