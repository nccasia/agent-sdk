"""``MiniMaxClient`` — MiniMax over its Anthropic-compatible endpoint.

MiniMax (M2/M3) speaks the Anthropic Messages API, so it *is* an
:class:`AnthropicClient` — same streaming, usage, retries, env wiring. It
differs in two provider quirks the base ``AnthropicClient`` (a faithful
passthrough — real Anthropic never does either) does not:

1. **Markup tool calls.** MiniMax sometimes emits a tool call as XML *markup*
   inside a text block instead of a native ``tool_use`` block:

       <minimax:tool_call><invoke name="write_file">
         <parameter name="path">ARCHITECTURE.md</parameter>
         <parameter name="content">…</parameter>
       </invoke></minimax:tool_call>

   We recover that markup into real ``tool_use`` blocks. Recovery is
   *truncation-tolerant*: a long call (e.g. a big file's ``content``) can be cut
   off by ``max_tokens`` mid-markup, dropping the closing tags — we still
   recover it by matching to the end of the text.

2. **Inlined reasoning.** MiniMax is a reasoning model that emits its
   chain-of-thought inside a ``<think>…</think>`` block at the head of a *text*
   block (not as a native Anthropic ``thinking`` block, which the engine already
   filters out). Left in, that reasoning leaks into the user-facing answer — a
   browser hides the unknown ``<think>`` tag and renders the inner reasoning as
   prose ahead of the real reply. We strip it here so the answer is clean and
   the replayed history is not polluted with the model's own scratch reasoning.
   A truncated block (``max_tokens`` cut mid-thought, no closing tag) is all
   reasoning and is dropped too.
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
# Reasoning block. Truncation-tolerant: an unterminated block (max_tokens cut
# mid-thought) is all reasoning and is matched to the end of the text (``\Z``).
_THINK_RE = re.compile(r"<think>.*?(?:</think>|\Z)", re.DOTALL)


def _strip_think(text: str) -> str:
    """Drop an inlined ``<think>…</think>`` reasoning block from a text block."""
    if "<think>" not in text:
        return text
    return _THINK_RE.sub("", text).strip()


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

    def _strip_reasoning(self, resp: Any) -> Any:
        """Strip inlined ``<think>…</think>`` reasoning from text blocks.

        Passthrough when no text block carries a ``<think>`` tag, so a native
        response is returned unchanged. Otherwise the text blocks are rebuilt with
        the reasoning removed (an emptied block is dropped); ``tool_use`` and
        native ``thinking`` blocks are preserved as-is.
        """
        blocks = getattr(resp, "content", []) or []
        if not any(
            getattr(b, "type", None) == "text" and "<think>" in (getattr(b, "text", "") or "")
            for b in blocks
        ):
            return resp
        rebuilt: list[Any] = []
        for b in blocks:
            if getattr(b, "type", None) == "text":
                cleaned = _strip_think(getattr(b, "text", "") or "")
                if cleaned:
                    rebuilt.append(TextBlock(text=cleaned))
            else:
                rebuilt.append(b)
        return Message(
            content=rebuilt,
            stop_reason=getattr(resp, "stop_reason", "end_turn"),
            usage=self._usage(resp),
        )

    def _postprocess(self, resp: Any) -> Any:
        """Strip inlined reasoning, then recover markup-emitted tool calls.

        First ``<think>…</think>`` reasoning is stripped from text blocks (so it
        never reaches the user-facing answer or the replayed history). Then native
        ``tool_use`` responses pass through unchanged, while a tool call emitted as
        ``<invoke name=…>`` markup inside a text block is parsed into ``tool_use``
        blocks and returned as a reconstructed :class:`Message` with
        ``stop_reason="tool_use"`` so the engine executes it.
        """
        resp = self._strip_reasoning(resp)
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
