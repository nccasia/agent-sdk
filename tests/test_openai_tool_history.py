"""Replayed tool turns reach an OpenAI-compatible gateway as real function calls.

Flattening a prior ``tool_use`` into assistant text (``[called kg.read({...})]``)
teaches the model to *write* its next call instead of making it: the loop then ends
on that text, the answer carries no citations, and the filter refuses.
"""

from __future__ import annotations

import json

from agent_sdk.clients.openai_client import OpenAIClient

TOOLS = [{"name": "kg.query", "description": "", "input_schema": {"type": "object"}}]


def _history() -> list[dict]:
    return [
        {"role": "user", "content": "Không join ncc8 thứ 6 thì bị phạt gì?"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Để mình tra."},
                {"type": "tool_use", "id": "call-1", "name": "kg.query", "input": {"q": "ncc8"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call-1", "content": "[doc:06#p21] …"}
            ],
        },
    ]


def _convert(messages: list[dict], tools: list[dict] | None = TOOLS) -> list[dict]:
    _, wire_to_canonical = OpenAIClient._to_openai_tools(tools)
    return OpenAIClient._to_openai_messages("sys", messages, wire_to_canonical)


def test_prior_tool_use_is_replayed_as_a_function_call_not_text():
    out = _convert(_history())

    assistant = out[2]
    assert assistant["role"] == "assistant"
    assert "[called" not in (assistant["content"] or "")
    assert assistant["content"] == "Để mình tra."
    (call,) = assistant["tool_calls"]
    assert call["id"] == "call-1"
    assert call["type"] == "function"
    # The wire name matches the sanitized name in the ``tools`` payload.
    assert call["function"]["name"] == "kg_query"
    assert json.loads(call["function"]["arguments"]) == {"q": "ncc8"}


def test_tool_result_is_a_tool_message_answering_its_call():
    out = _convert(_history())

    assert out[3] == {"role": "tool", "tool_call_id": "call-1", "content": "[doc:06#p21] …"}
    assert len(out) == 4


def test_every_call_is_answered_even_when_its_result_is_missing():
    history = _history()[:2]  # the result was dropped (e.g. compacted away)

    out = _convert(history)

    assert out[-1] == {"role": "tool", "tool_call_id": "call-1", "content": ""}


def test_a_result_with_no_open_call_stays_as_user_text():
    history = [
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "gone", "content": "x"}],
        }
    ]

    out = _convert(history)

    assert out[1] == {"role": "user", "content": "x"}


def test_block_list_tool_result_content_is_flattened_to_text():
    history = _history()
    history[2]["content"][0]["content"] = [
        {"type": "text", "text": "a"},
        {"type": "text", "text": "b"},
    ]

    out = _convert(history)

    assert out[3]["content"] == "a\nb"


def test_a_call_to_a_tool_no_longer_offered_is_still_sanitized():
    out = _convert(_history(), tools=None)

    assert out[2]["tool_calls"][0]["function"]["name"] == "kg_query"


def test_user_text_after_tool_results_follows_the_tool_messages():
    history = _history()
    history[2]["content"].append({"type": "text", "text": "tiếp đi"})

    out = _convert(history)

    assert [m["role"] for m in out[3:]] == ["tool", "user"]
    assert out[4]["content"] == "tiếp đi"
