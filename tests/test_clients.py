"""LLM clients — FakeClient scripting, MixedClient routing, shorthand."""

from __future__ import annotations

from agent_sdk.clients import (
    AnthropicClient,
    FakeClient,
    Message,
    MixedClient,
    OpenAIClient,
    make_client,
)


async def test_fake_text_answer():
    c = FakeClient(["Hello there."])
    msg = await c(stage="synthesize", system="sys", messages=[], max_tokens=100)
    assert msg.stop_reason == "end_turn"
    assert msg.text == "Hello there."
    assert c.total_usage.output_tokens > 0


async def test_fake_tool_call_then_answer():
    c = FakeClient(
        [
            {"tools": [{"name": "search", "input": {"query": "x"}}]},
            "Final answer.",
        ]
    )
    m1 = await c(
        stage="research", system="s", messages=[], max_tokens=100, tools=[{"name": "search"}]
    )
    assert m1.stop_reason == "tool_use"
    assert m1.tool_uses[0].name == "search"
    assert m1.tool_uses[0].input == {"query": "x"}
    m2 = await c(stage="research", system="s", messages=[], max_tokens=100)
    assert m2.stop_reason == "end_turn"
    assert m2.text == "Final answer."


async def test_fake_default_when_exhausted():
    c = FakeClient([], default="fallback")
    msg = await c(stage="x", system="", messages=[], max_tokens=10)
    assert msg.text == "fallback"


async def test_fake_records_calls():
    c = FakeClient(["ok"])
    await c(
        stage="synthesize",
        system="SYS",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=50,
    )
    assert c.calls[0]["stage"] == "synthesize"
    assert c.calls[0]["system"] == "SYS"


async def test_fake_handler_callable():
    def handler(stage, system, messages, tools):
        return f"stage={stage}"

    c = FakeClient([handler])
    msg = await c(stage="classify", system="", messages=[], max_tokens=10)
    assert msg.text == "stage=classify"


async def test_mixed_routes_per_stage():
    classify = FakeClient(["SIMPLE"])
    synth = FakeClient(["The answer."])
    default = FakeClient(["default"])
    mixed = MixedClient(default=default, classify=classify, synthesize=synth)

    m1 = await mixed(stage="classify", system="", messages=[], max_tokens=10)
    assert m1.text == "SIMPLE"
    m2 = await mixed(stage="synthesize", system="", messages=[], max_tokens=10)
    assert m2.text == "The answer."
    m3 = await mixed(stage="other", system="", messages=[], max_tokens=10)
    assert m3.text == "default"
    # aggregate usage spans sub-clients
    assert mixed.total_usage.output_tokens > 0


def test_make_client_shorthand():
    assert isinstance(make_client("claude-opus-4-6"), AnthropicClient)
    assert isinstance(make_client("gpt-4.1"), OpenAIClient)
    fc = FakeClient(["x"])
    assert make_client(fc) is fc


def test_message_helpers():
    from agent_sdk.clients.messages import TextBlock, ToolUseBlock

    msg = Message(content=[TextBlock(text="hi"), ToolUseBlock(id="1", name="t", input={})])
    assert msg.text == "hi"
    assert msg.tool_uses[0].name == "t"


def test_minimax_routing_and_defaults():
    from agent_sdk.clients import MiniMaxClient, make_client

    assert isinstance(make_client("MiniMax-M2.7"), MiniMaxClient)
    assert isinstance(make_client("abab-6.5"), MiniMaxClient)
    assert MiniMaxClient().model == "MiniMax-M2.7"  # provider default
    assert MiniMaxClient.provider == "minimax"


def test_anthropic_is_faithful_passthrough():
    """The base Anthropic client never rewrites responses (no markup recovery)."""
    from types import SimpleNamespace

    from agent_sdk.clients import AnthropicClient

    markup = '<minimax:tool_call>\n<invoke name="x"><parameter name="a">1</parameter></invoke>'
    resp = SimpleNamespace(stop_reason="end_turn",
                           content=[SimpleNamespace(type="text", text=markup)], usage=None)
    assert AnthropicClient("m")._postprocess(resp) is resp  # passthrough, markup untouched


def test_minimax_recovers_markup_tool_calls():
    from types import SimpleNamespace

    from agent_sdk.clients import MiniMaxClient

    markup = (
        'Sure, writing the doc.\n'
        '<minimax:tool_call>\n<invoke name="write_file">\n'
        '<parameter name="path">ARCHITECTURE.md</parameter>\n'
        '<parameter name="content"># Title\n\nBody line.</parameter>\n'
        '</invoke>\n</minimax:tool_call>'
    )
    resp = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=markup)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
    )
    out = MiniMaxClient()._postprocess(resp)
    assert out.stop_reason == "tool_use"
    tu = out.tool_uses[0]
    assert tu.name == "write_file"
    assert tu.input["path"] == "ARCHITECTURE.md"
    assert tu.input["content"] == "# Title\n\nBody line."
    assert out.text.strip() == "Sure, writing the doc."  # markup stripped


def test_minimax_recovered_ids_are_unique_across_hops():
    """Recovered tool-call ids must not collide across messages — duplicate ids make
    the Anthropic-compatible API reject the round-tripped tool_result."""
    from types import SimpleNamespace

    from agent_sdk.clients import MiniMaxClient

    def _markup(name):
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text",
                                     text=f'<invoke name="{name}"><parameter name="x">1</parameter></invoke>')],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    client = MiniMaxClient()  # one client = one conversation
    ids = [client._postprocess(_markup("Read")).tool_uses[0].id for _ in range(4)]
    assert ids == ["markup_0", "markup_1", "markup_2", "markup_3"]
    assert len(set(ids)) == 4  # all unique across hops


def test_clients_have_finite_request_timeout():
    """A finite per-request timeout so a stalled provider call fails fast instead of
    hanging the turn (the anthropic SDK default is 600s)."""
    from agent_sdk.clients import AnthropicClient, MiniMaxClient

    assert AnthropicClient().timeout == 300.0
    assert MiniMaxClient().timeout == 300.0  # inherited via **kwargs
    assert AnthropicClient(timeout=45.0).timeout == 45.0  # overridable


def test_minimax_passthrough_on_native_tool_use():
    from types import SimpleNamespace

    from agent_sdk.clients import MiniMaxClient

    resp = SimpleNamespace(stop_reason="tool_use", content=[], usage=None)
    assert MiniMaxClient()._postprocess(resp) is resp  # native → unchanged


def test_minimax_recovers_truncated_markup():
    from types import SimpleNamespace

    from agent_sdk.clients import MiniMaxClient

    # max_tokens cut the call off mid-content → no closing tags
    markup = ('writing… <minimax:tool_call>\n<invoke name="bash">\n'
              '<parameter name="command">cat > F.md << EOF\n# Title\nlots of content that got cut')
    resp = SimpleNamespace(stop_reason="max_tokens",
                           content=[SimpleNamespace(type="text", text=markup)], usage=None)
    out = MiniMaxClient()._postprocess(resp)
    assert out.stop_reason == "tool_use"
    assert out.tool_uses[0].name == "bash"
    assert "cat > F.md" in out.tool_uses[0].input["command"]
