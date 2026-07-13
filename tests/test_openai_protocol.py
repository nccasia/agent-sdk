from __future__ import annotations

import pytest

from agent_sdk.clients.openai_client import OpenAIClient, ProviderResponseError


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


@pytest.mark.parametrize(
    ("status_code", "expected_class"),
    [
        (1002, "rate_limit"),
        (1008, "insufficient_quota"),
        (1039, "request_capacity"),
        (1026, "content_policy"),
    ],
)
def test_native_openai_client_normalizes_minimax_base_response_errors(
    status_code: int, expected_class: str
):
    with pytest.raises(ProviderResponseError) as exc_info:
        OpenAIClient._adapt(
            {
                "base_resp": {
                    "status_code": status_code,
                    "status_msg": "provider diagnostic must stay private",
                }
            }
        )

    assert exc_info.value.provider_failure_class == expected_class
    assert "provider diagnostic" not in str(exc_info.value)


def test_native_openai_client_normalizes_openrouter_error_completion():
    with pytest.raises(ProviderResponseError) as exc_info:
        OpenAIClient._adapt(
            {
                "choices": [
                    {
                        "finish_reason": "error",
                        "error": {
                            "message": "provider diagnostic must stay private",
                            "metadata": {"error_type": "provider_unavailable"},
                        },
                    }
                ]
            }
        )

    assert exc_info.value.provider_failure_class == "server_error"
    assert "provider diagnostic" not in str(exc_info.value)
