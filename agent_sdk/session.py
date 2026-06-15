"""``Session`` — persisted conversation state (pluggable backend).

A ``Session`` is a small handle bundling an ``id`` and a backing ``store``. It
carries the rolling conversation — history + summary + extracted facts + any
per-conversation injected context — loaded at turn start, appended + compacted at
turn end. There is **no separate context store**: per-conversation injected
context lives here; durable cross-conversation context lives in ``Memory``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Turn", "SessionState", "Session", "SNAPSHOT_VERSION"]

# Snapshot schema version (the stateless-serving contract). Bumped only on a
# breaking change to ``SessionState.to_json``. ``from_json`` is forward/backward
# tolerant — unknown keys are ignored, missing keys default — so most additive
# extensions need no bump; this stamp is the explicit hook for a future migration.
SNAPSHOT_VERSION = 1


def _clip(text: str, limit: int) -> str:
    """Shorten a long turn to ``limit`` chars, keeping head + tail with an elision marker."""
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    return f"{text[:head]}\n[… {len(text) - limit} chars elided …]\n{text[-tail:]}"


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    content: str
    metadata: dict = field(default_factory=dict)

    def to_message(self) -> dict:
        return {"role": self.role, "content": self.content}

    def to_json(self) -> dict:
        return {"role": self.role, "content": self.content, "metadata": self.metadata}

    @classmethod
    def from_json(cls, d: dict) -> Turn:
        return cls(role=d["role"], content=d.get("content", ""), metadata=d.get("metadata", {}))


@dataclass
class SessionState:
    history: list[Turn] = field(default_factory=list)
    summary: str = ""
    facts: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)
    # Skills the model has activated in this conversation (RFC 0013). Persisted so
    # the skill_active lobe keeps driving a loaded SOP across turns (set by the
    # engine from the turn's ``skills_in_use`` at the ActivateSkill moment).
    skills_in_use: list[str] = field(default_factory=list)
    # Flow/path bias the metacognition meta-control tool recorded last turn. Flow
    # is resolved once at turn start (a pure function of (spec, context)), so a
    # mid-turn meta decision cannot retarget the current turn — it is persisted
    # here and folded into the next turn's recognition context as a deterministic
    # signal (the MetacognitionPlugin's path recognizer reads it). Empty ⇒ no bias.
    meta_flow_bias: str = ""
    # Universal-memory snapshot (``MemoryStore.to_json()`` — the durable ``_long`` tier + offloaded
    # bodies). Carried here so a Session is the COMPLETE per-session state: persist/restore it and
    # the agent's working memory survives a stateless hop (new process / different replica). Empty
    # ⇒ no memory (or universal memory disabled). The agent restores it on load and captures it on
    # finalize; opaque to the Session itself.
    memory: dict = field(default_factory=dict)

    def messages(
        self, *, first_n: int = 1, last_m: int = 6, max_turn_chars: int = 2000
    ) -> list[dict]:
        """The conversation as provider messages — a trimmed transcript (primacy + recency).

        A short conversation (``len(history) <= last_m`` with no summary) renders verbatim, as
        before. A long one is shaped so it doesn't bloat context:

        - **n first** turns kept (primacy: the task framing), folded with the rolling ``summary``
          + a ``[… k earlier turns elided …]`` marker into one ``[Conversation so far]`` block;
        - **the middle** is blurred (elided / covered by the summary);
        - **n last** turns kept as native messages, each **capped** to ``max_turn_chars``
          (recency: the live thread).
        """
        h = self.history
        if not self.summary and len(h) <= last_m:
            return [t.to_message() for t in h]  # short: verbatim (unchanged behavior)

        out: list[dict] = []
        blocks: list[str] = []
        if self.summary:
            blocks.append(self.summary.strip())
        if len(h) > last_m:
            older = h[:-last_m]
            tail = h[-last_m:]
            first = older[: max(0, first_n)]
            if first:
                blocks.append(
                    "\n".join(
                        f"{'U' if t.role == 'user' else 'A'}: {_clip(t.content, max_turn_chars)}"
                        for t in first
                    )
                )
            elided = len(older) - len(first)
            if elided > 0:
                blocks.append(f"[… {elided} earlier turns elided …]")
        else:
            tail = h
        if blocks:
            out.append({"role": "user", "content": "[Conversation so far]\n" + "\n".join(blocks)})
        out.extend({"role": t.role, "content": _clip(t.content, max_turn_chars)} for t in tail)
        return out

    def transcript(self, *, first_n: int = 1, last_m: int = 6, max_turn_chars: int = 2000) -> str:
        """The trimmed conversation rendered as a ``U:/A:`` transcript (same primacy/recency
        shaping as :meth:`messages`). For a lobe that wants the dialog as a prompt *section*
        rather than native messages."""
        lines: list[str] = []
        for m in self.messages(first_n=first_n, last_m=last_m, max_turn_chars=max_turn_chars):
            tag = "U" if m.get("role") == "user" else "A"
            content = str(m.get("content", "")).strip()
            if content:
                lines.append(f"{tag}: {content}")
        return "\n".join(lines)

    def to_json(self) -> dict:
        """The full per-session snapshot (the stateless-serving contract). JSON-only — persist it
        in any ``SessionStore`` (Redis/SQL/…) or hand it straight to ``PreactAgent.run_snapshot``.
        ``v`` stamps the schema version for future migrations; new fields are additive."""
        return {
            "v": SNAPSHOT_VERSION,
            "history": [t.to_json() for t in self.history],
            "summary": self.summary,
            "facts": self.facts,
            "context": self.context,
            "skills_in_use": self.skills_in_use,
            "meta_flow_bias": self.meta_flow_bias,
            "memory": self.memory,
        }

    @classmethod
    def from_json(cls, d: dict | None) -> SessionState:
        """Rebuild from :meth:`to_json`. Tolerant by design (the extensibility seam): unknown keys
        ignored, missing keys defaulted — so an older snapshot loads into a newer SDK and vice
        versa. Branch on ``d.get("v")`` here if a future version needs an explicit migration."""
        d = d or {}
        return cls(
            history=[Turn.from_json(t) for t in d.get("history", [])],
            summary=d.get("summary", ""),
            facts=list(d.get("facts", [])),
            context=list(d.get("context", [])),
            skills_in_use=list(d.get("skills_in_use", [])),
            meta_flow_bias=str(d.get("meta_flow_bias", "")),
            memory=dict(d.get("memory") or {}),
        )


# A summarizer folds a window of turns into a summary string.
Summarizer = Callable[[list[Turn]], Awaitable[str]]


class Session:
    """A conversation handle: an ``id`` + a backing ``SessionStore``.

    Defaults to an in-memory store when none is given (zero infra).
    """

    def __init__(self, id: str, store: Any | None = None):
        self.id = id
        if store is None:
            from agent_sdk.stores.session import SessionStoreInMemory

            store = SessionStoreInMemory()
        self.store = store

    async def load(self) -> SessionState:
        return await self.store.load(self.id)

    async def append(self, turn: Turn) -> None:
        await self.store.append(self.id, turn)

    async def save(self, state: SessionState) -> bool:
        """Persist the WHOLE state atomically (history + memory + skills_in_use + meta_flow_bias),
        if the store supports it. Returns False when the store only does ``append`` (legacy /
        host-owned durability), so the caller can fall back. This is what lets a snapshot carry
        more than the transcript across a stateless hop."""
        saver = getattr(self.store, "save", None)
        if saver is None:
            return False
        await saver(self.id, state)
        return True

    async def compact(self, summarizer: Summarizer, *, keep_last: int = 6) -> None:
        await self.store.compact(self.id, summarizer, keep_last=keep_last)
