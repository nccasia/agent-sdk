"""OpenAI-compatible tool-name normalization.

OpenAI and many compatible gateways (DeepSeek, etc.) require function names to
match ``^[a-zA-Z0-9_-]+$``. Engine tool ids often use dots (``kb.search``,
``tasks.create``); sanitize on the wire and map responses back to the canonical id.
"""

from __future__ import annotations

import re

__all__ = [
    "sanitize_openai_tool_name",
    "openai_tools_payload",
    "restore_tool_name",
]

_INVALID_TOOL_CHAR = re.compile(r"[^a-zA-Z0-9_-]")
_COLLAPSE_UNDERS = re.compile(r"_+")


def sanitize_openai_tool_name(name: str) -> str:
    safe = _INVALID_TOOL_CHAR.sub("_", str(name or "").strip())
    safe = _COLLAPSE_UNDERS.sub("_", safe).strip("_")
    return safe or "tool"


def openai_tools_payload(
    tools: list[dict] | None,
) -> tuple[list[dict] | None, dict[str, str]]:
    """Build OpenAI ``tools`` and a wire-name → canonical-name map."""
    if not tools:
        return None, {}
    out: list[dict] = []
    wire_to_canonical: dict[str, str] = {}
    for spec in tools:
        canonical = str(spec.get("name") or "tool")
        wire = sanitize_openai_tool_name(canonical)
        base = wire
        suffix = 2
        while wire in wire_to_canonical and wire_to_canonical[wire] != canonical:
            wire = f"{base}_{suffix}"
            suffix += 1
        wire_to_canonical[wire] = canonical
        out.append(
            {
                "type": "function",
                "function": {
                    "name": wire,
                    "description": spec.get("description", ""),
                    "parameters": spec.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
        )
    return out, wire_to_canonical


def restore_tool_name(wire_name: str, wire_to_canonical: dict[str, str]) -> str:
    return wire_to_canonical.get(wire_name or "", wire_name or "")
