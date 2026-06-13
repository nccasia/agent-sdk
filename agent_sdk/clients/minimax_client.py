"""``MiniMaxClient`` — MiniMax over its Anthropic-compatible endpoint.

MiniMax (M2/M3) speaks the Anthropic Messages API, so it *is* an
:class:`AnthropicClient` — same streaming, usage, retries, env wiring. It only
differs in one provider quirk: MiniMax sometimes emits a tool call as XML
*markup* inside a text block instead of a native ``tool_use`` block:

    <minimax:tool_call><invoke name="write_file">
      <parameter name="path">ARCHITECTURE.md</parameter>
      <parameter name="content">…</parameter>
    </invoke></minimax:tool_call>

This client recovers that markup into real ``tool_use`` blocks (the base
``AnthropicClient`` stays a faithful passthrough — real Anthropic never emits
this). Recovery is *truncation-tolerant*: a long call (e.g. a big file's
``content``) can be cut off by ``max_tokens`` mid-markup, dropping the closing
tags — we still recover it by matching to the end of the text.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent_sdk.clients.anthropic_client import AnthropicClient
from agent_sdk.clients.messages import Message, TextBlock, ToolUseBlock

__all__ = ["MiniMaxClient"]

# Tolerant of TRUNCATION: when the closing tag is absent (call cut off by
# max_tokens), match to the end of the text (``\Z``).
_INVOKE_RE = re.compile(
    r"<invoke\s+name=\"(?P<name>[^\"]+)\">(?P<body>.*?)(?:</invoke>|\Z)", re.DOTALL
)
_PARAM_RE = re.compile(
    r"<parameter\s+name=\"(?P<k>[^\"]+)\">(?P<v>.*?)(?:</parameter>|\Z)", re.DOTALL
)
_MARKUP_STRIP_RE = re.compile(
    r"<minimax:tool_call>.*?(?:</minimax:tool_call>|\Z)|<invoke\s+name=.*?(?:</invoke>|\Z)",
    re.DOTALL,
)


def _parse_markup_tool_calls(text: str, start: int = 0) -> list[ToolUseBlock]:
    """Parse ``<invoke name=…>`` markup in a text block into tool_use blocks.

    ``start`` seeds the id counter so ids are unique across the WHOLE conversation,
    not just within one message — duplicate ``markup_0`` ids across hops make the
    Anthropic-compatible API reject the round-tripped tool_result ("tool id not found").
    """
    out: list[ToolUseBlock] = []
    for i, m in enumerate(_INVOKE_RE.finditer(text)):
        params: dict[str, Any] = {}
        for p in _PARAM_RE.finditer(m.group("body")):
            raw = p.group("v")
            try:  # numbers/bools/objects come through as JSON; prose stays a string
                params[p.group("k")] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                params[p.group("k")] = raw
        out.append(ToolUseBlock(id=f"markup_{start + i}", name=m.group("name"), input=params))
    return out


class MiniMaxClient(AnthropicClient):
    """MiniMax via the Anthropic-compatible endpoint, with markup-tool recovery."""

    provider = "minimax"

    def __init__(self, model: str = "MiniMax-M2.7", **kwargs: Any):
        super().__init__(model, **kwargs)
        self._markup_seq = 0  # monotonic id seed for recovered tool calls (unique per conversation)

    def _postprocess(self, resp: Any) -> Any:
        """Recover markup-emitted tool calls into real ``tool_use`` blocks.

        Native ``tool_use`` responses pass through unchanged. A tool call emitted
        as ``<invoke name=…>`` markup inside a text block is parsed into
        ``tool_use`` blocks and returned as a reconstructed :class:`Message` with
        ``stop_reason="tool_use"`` so the engine executes it.
        """
        if getattr(resp, "stop_reason", None) == "tool_use":
            return resp
        blocks = getattr(resp, "content", []) or []
        text = "\n".join(
            getattr(b, "text", "") for b in blocks if getattr(b, "type", None) == "text"
        )
        if "<invoke name=" not in text:
            return resp
        tool_uses = _parse_markup_tool_calls(text, self._markup_seq)
        if not tool_uses:
            return resp
        self._markup_seq += len(tool_uses)  # advance so the next hop's ids never collide
        cleaned = _MARKUP_STRIP_RE.sub("", text).strip()
        content: list[Any] = []
        if cleaned:
            content.append(TextBlock(text=cleaned))
        content.extend(tool_uses)
        return Message(content=content, stop_reason="tool_use", usage=self._usage(resp))
