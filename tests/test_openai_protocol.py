from __future__ import annotations

import pytest

from agent_sdk.clients.openai_client import OpenAIClient


def test_native_openai_client_rejects_string_response_without_leaking_payload():
    with pytest.raises(Exception, match="invalid_response_type") as exc_info:
        OpenAIClient._adapt("gateway diagnostic: bearer secret-value")

    assert "secret-value" not in str(exc_info.value)


def test_native_openai_client_strips_thinking_and_restores_canonical_tool_name():
    message = OpenAIClient._adapt(
        {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "<think>internal reasoning</think>Answer",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {"name": "kb_search", "arguments": '{"q":"x"}'},
                            }
                        ],
                    },
                }
            ]
        },
        {"kb_search": "kb.search"},
    )

    assert message.content[0].text == "Answer"
    assert message.content[1].name == "kb.search"
