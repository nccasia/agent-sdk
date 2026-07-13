"""Tests for OpenAI-compatible tool name normalization."""

from __future__ import annotations

from agent_sdk.clients.openai_tools import (
    openai_tools_payload,
    restore_tool_name,
    sanitize_openai_tool_name,
)


def test_sanitize_replaces_dots_and_invalid_chars():
    assert sanitize_openai_tool_name("kb.search") == "kb_search"
    assert sanitize_openai_tool_name("tasks.create") == "tasks_create"
    assert sanitize_openai_tool_name("admin.bot.save") == "admin_bot_save"


def test_openai_tools_payload_maps_wire_names_back():
    tools, wire_map = openai_tools_payload(
        [
            {"name": "kb.search", "description": "search", "input_schema": {"type": "object"}},
            {"name": "tasks.create", "description": "create", "input_schema": {"type": "object"}},
        ]
    )
    assert tools is not None
    assert tools[0]["function"]["name"] == "kb_search"
    assert tools[1]["function"]["name"] == "tasks_create"
    assert restore_tool_name("kb_search", wire_map) == "kb.search"


def test_openai_tools_payload_avoids_wire_name_collisions():
    tools, wire_map = openai_tools_payload(
        [
            {"name": "a.b", "description": "", "input_schema": {"type": "object"}},
            {"name": "a-b", "description": "", "input_schema": {"type": "object"}},
        ]
    )
    assert tools is not None
    names = [t["function"]["name"] for t in tools]
    assert len(set(names)) == 2
    assert wire_map[names[0]] in {"a.b", "a-b"}
    assert wire_map[names[1]] in {"a.b", "a-b"}
