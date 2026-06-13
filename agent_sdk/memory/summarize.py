"""Densification — turn a full body into a dense digest (the gist), not a truncation.

Two summarizers fill one role (see ``docs/concepts/universal-memory.md`` ·
``tool-use-at-scale.md``):

* :func:`deterministic_digest` — free, synchronous, structured-extract. Preserves the
  decision-relevant content (paths, identifiers, numbers, the head line) and drops chatter.
  This is what runs in the hot funnel loop and in CI — no model call.
* :func:`llm_digest` — an **async** cheap-model summary (Claude Code's split/summarize/merge,
  preservation-first prompt). Used for explicit densification (promotion to long-term, the
  live benchmark) — NOT in the sync funnel seam, which must stay free.

A digest never loses the detail: the caller (the memory store) names the body's handle next to
the digest, so it is always re-fetchable.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from agent_sdk.skills import est_tokens

__all__ = ["deterministic_digest", "llm_digest", "brief_args", "DEFAULT_DIGEST_PROMPT"]

# A line is "salient" if it carries decision-relevant signal: a number, a path/identifier,
# an ALLCAPS token (constants/labels), or a key:value. These are the needles a digest must keep.
_SALIENT_RE = re.compile(r"\d|[A-Z]{3,}|[\w./-]+\.[a-zA-Z]{1,5}\b|/[\w./-]+|:\s*\S")


def brief_args(args: Any, limit: int = 48) -> str:
    try:
        s = json.dumps(args, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        s = str(args)
    s = " ".join(s.split())
    return s[:limit] + ("…" if len(s) > limit else "")


def _excerpt(text: str, limit: int) -> str:
    s = " ".join((text or "").split())
    return s[:limit] + ("…" if len(s) > limit else "")


def deterministic_digest(
    kind: str,
    meta: dict | None,
    body: str,
    *,
    max_chars: int = 240,
    max_salient: int = 3,
) -> str:
    """A free, deterministic dense digest of ``body`` for a ``kind`` entry.

    Keeps the head line plus the most salient lines (numbers / paths / identifiers / key:value)
    — the needles a later step might need — labeled by kind and (for tool results) the tool+args.
    Deterministic: same input → same digest.
    """
    meta = meta or {}
    lines = [ln.strip() for ln in (body or "").splitlines() if ln.strip()]
    head = lines[0] if lines else ""
    salient: list[str] = []
    for ln in lines[1:]:
        if _SALIENT_RE.search(ln):
            salient.append(ln)
        if len(salient) >= max_salient:
            break
    gist = _excerpt(" · ".join([head, *salient]) if salient else head, max_chars)
    tool = meta.get("tool")
    label = f"{tool}({brief_args(meta.get('args', meta.get('input', {})))})" if tool else kind
    return f"[{kind}] {label} → {gist}" if gist else f"[{kind}] {label}"


DEFAULT_DIGEST_PROMPT = (
    "You compress an agent's working notes into a dense digest. Preserve EVERY decision, file "
    "path, identifier, number, and open TODO verbatim. Drop confirmations, pleasantries, and "
    "transient chatter. Output only the digest — no preamble. Keep it under {max_tokens} tokens."
)


async def llm_digest(
    client: Any,
    kind: str,
    meta: dict | None,
    body: str,
    *,
    max_tokens: int = 256,
    stage: str = "memory.digest",
    prompt: str | None = None,
) -> str:
    """An async cheap-model digest — the preservation-first summary (split/summarize/merge is
    the caller's batching; this summarizes one block). Falls back to the deterministic digest on
    any client error so densification never raises in the loop."""
    system = (prompt or DEFAULT_DIGEST_PROMPT).format(max_tokens=max_tokens)
    user = f"kind={kind} meta={brief_args(meta or {}, 120)}\n\n{body}"
    try:
        msg = await client(
            stage=stage,
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=max_tokens,
            temperature=0.0,
        )
        text = _message_text(msg).strip()
        return text or deterministic_digest(kind, meta, body, max_chars=max_tokens * 4)
    except Exception:
        return deterministic_digest(kind, meta, body, max_chars=max_tokens * 4)


def _message_text(msg: Any) -> str:
    """Best-effort text extraction from an SDK client response."""
    content = getattr(msg, "content", msg)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            t = getattr(b, "text", None)
            if t is None and isinstance(b, dict):
                t = b.get("text")
            if t:
                parts.append(t)
        return "\n".join(parts)
    return str(content or "")


def compression_ratio(digest: str, body: str) -> float:
    """digest tokens / body tokens — small is dense. 1.0 if the body is empty."""
    b = est_tokens(body) or 1
    return round(est_tokens(digest) / b, 4)


# A summarizer is ``(kind, meta, body) -> digest`` (sync). The store binds max_chars.
Summarizer = Callable[[str, dict, str], str]
