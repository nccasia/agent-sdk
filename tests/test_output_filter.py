"""The output-filter seam — a plugin's deterministic ``(text) -> text`` reshape of
the rendered reply.

A plugin registers ``setup.add_output_filter(fn, stage="respond")`` and the engine
applies it to the reply-rendering (terminal) stage's text — no extra LLM call, pure
presentation. Locks the seam in: filters run on the rendered reply, compose in
registration order, and the default (no filter) path is byte-identical.
"""

from __future__ import annotations

from agent_sdk import PreactAgent
from agent_sdk.clients.fake import FakeClient


class _FilterPlugin:
    name = "filter_probe"

    def __init__(self, fn, *, stage="respond"):
        self.fn = fn
        self.stage = stage

    def install(self, setup):
        setup.add_output_filter(self.fn, stage=self.stage)


def _agent(plugins=None, default="hello world") -> PreactAgent:
    return PreactAgent(client=FakeClient(default=default), plugins=plugins or [])


async def test_output_filter_reshapes_the_rendered_reply():
    agent = _agent([_FilterPlugin(str.upper)])
    res = await agent.query("hi")
    assert res.text == "HELLO WORLD"
    assert res.status == "answered"


async def test_filters_compose_in_registration_order():
    # first filter appends " a", second appends " b" → order is observable.
    agent = _agent([
        _FilterPlugin(lambda t: t + " a"),
        _FilterPlugin(lambda t: t + " b"),
    ])
    res = await agent.query("hi")
    assert res.text == "hello world a b"


async def test_no_filter_is_byte_identical():
    res = await _agent([]).query("hi")
    assert res.text == "hello world"
    assert res.status == "answered"


async def test_seam_wires_through_to_the_engine():
    agent = _agent([_FilterPlugin(str.upper)])
    assert len(agent.engine._output_filters) == 1
    assert agent.engine._output_filters[0][0] == "respond"


async def test_non_matching_stage_target_is_not_applied():
    # A filter targeting a stage id that never runs leaves the reply untouched.
    agent = _agent([_FilterPlugin(str.upper, stage="nonexistent_stage")])
    res = await agent.query("hi")
    assert res.text == "hello world"


async def test_a_raising_filter_never_loses_the_turn():
    def boom(_text):
        raise ValueError("filter blew up")

    res = await _agent([_FilterPlugin(boom)]).query("hi")
    assert res.status == "answered"
    assert res.text == "hello world"  # original kept
