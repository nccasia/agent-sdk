"""Universal Memory — one entry type, one interface, two tiers.

Everything the agent touches is a :class:`MemoryEntry`: context, decisions, notes, tool results,
temp files, facts. Each is stored as a dense ``digest`` (the gist, what the prompt holds) plus an
offloaded ``body`` (the detail, re-fetchable by ``handle``), valued by CDS. The store has two tiers
by scope — FLASH (``turn``, in-RAM, dropped at turn end) and LONG-TERM
(``conversation``/``channel``/``user``/``bot``, durable) — and routes large bodies to a
:class:`~agent_sdk.react.docworkspace.DocWorkspace` so they are sliceable, never resident whole.

See ``docs/concepts/06-universal-memory.md``. This is the substrate; the ``tool_result`` kind is the
first application (``docs/concepts/05-tool-use-at-scale.md``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from agent_sdk.memory.summarize import Summarizer, deterministic_digest
from agent_sdk.network.context_builder import score_relevance
from agent_sdk.react.docworkspace import DocWorkspace
from agent_sdk.skills import est_tokens

__all__ = ["MemoryEntry", "MemoryStore", "FLASH_SCOPE", "LONG_TERM_SCOPES", "KIND_UTILITY"]

FLASH_SCOPE = "turn"
LONG_TERM_SCOPES = ("conversation", "channel", "user", "bot")

# Thought-steering weight per kind: how much having the entry in the palette steers the next
# decision (utility in CDS = relevance × utility / cost). A decision/plan steers hard and is tiny;
# a raw tool_result steers once then drops to its digest.
KIND_UTILITY: dict[str, float] = {
    "decision": 1.4,
    "plan": 1.4,
    "obligation": 1.3,
    "sub_goal": 1.2,
    "fact": 1.2,
    "note": 1.1,
    "hypothesis": 1.0,
    "context": 1.0,
    "artifact": 0.9,
    "tool_result": 0.9,
    "temp_file": 0.8,
}

# Bodies at or above this size offload to DocWorkspace (sliceable by grep/read_section).
_LARGE_BODY_CHARS = 2000

# Snapshot bounds (Redis offloading): a session's durable memory blob is capped so per-session
# Redis growth stays bounded. Pinned entries always survive; the rest are kept highest-CDS-then-
# newest-first until either cap is hit (lowest-value oldest entries are dropped = forgotten).
_SNAPSHOT_MAX_ENTRIES = 256
_SNAPSHOT_MAX_TOKENS = 16_000


@dataclass
class MemoryEntry:
    handle: str  # mem://<kind>/<scope>/<key>
    kind: str
    scope: str
    digest: str  # the gist — what the prompt holds
    body: str  # the detail — offloaded, re-fetchable
    utility: float = 1.0
    relevance: float = 0.0
    cds: float = 0.0
    tier: int = 0
    pinned: bool = False
    recency: float = 0.0  # higher = newer
    tokens: int = 0
    source: str = ""
    meta: dict = field(default_factory=dict)
    created_seq: int = 0
    offloaded: bool = False  # body lives in DocWorkspace (large)

    @property
    def is_flash(self) -> bool:
        return self.scope == FLASH_SCOPE

    def to_json(self) -> dict:
        return {
            "handle": self.handle,
            "kind": self.kind,
            "scope": self.scope,
            "digest": self.digest,
            "tokens": self.tokens,
            "utility": self.utility,
            "cds": round(self.cds, 4),
            "tier": self.tier,
            "pinned": self.pinned,
            "source": self.source,
            "meta": self.meta,
            "offloaded": self.offloaded,
        }

    def snapshot(self) -> dict:
        """Loss-free serialization for Redis offloading (carries ``body``/``recency``/
        ``created_seq`` that the prompt-facing :meth:`to_json` drops). Round-trips via
        :meth:`restore`."""
        return {
            "handle": self.handle,
            "kind": self.kind,
            "scope": self.scope,
            "digest": self.digest,
            "body": self.body,
            "utility": self.utility,
            "relevance": self.relevance,
            "cds": self.cds,
            "tier": self.tier,
            "pinned": self.pinned,
            "recency": self.recency,
            "tokens": self.tokens,
            "source": self.source,
            "meta": dict(self.meta),
            "created_seq": self.created_seq,
            "offloaded": self.offloaded,
        }

    @classmethod
    def restore(cls, d: dict) -> MemoryEntry:
        return cls(
            handle=d["handle"],
            kind=d["kind"],
            scope=d["scope"],
            digest=d.get("digest", ""),
            body=d.get("body", ""),
            utility=d.get("utility", 1.0),
            relevance=d.get("relevance", 0.0),
            cds=d.get("cds", 0.0),
            tier=d.get("tier", 0),
            pinned=d.get("pinned", False),
            recency=d.get("recency", 0.0),
            tokens=d.get("tokens", 0),
            source=d.get("source", ""),
            meta=dict(d.get("meta") or {}),
            created_seq=d.get("created_seq", 0),
            offloaded=d.get("offloaded", False),
        )


def _stringify(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(content)


_SLUG_RE = re.compile(r"[^a-z0-9]+")


class MemoryStore:
    """Two-tier universal memory. Pure/in-process; large bodies route to a DocWorkspace.

    ``summarizer(kind, meta, body) -> digest`` builds the gist (defaults to the free
    deterministic digest); ``cds_cost_unit`` calibrates the size penalty in CDS.
    """

    def __init__(
        self,
        *,
        summarizer: Summarizer | None = None,
        docworkspace: DocWorkspace | None = None,
        digest_max_chars: int = 240,
        large_body_chars: int = _LARGE_BODY_CHARS,
        cds_cost_unit: float = 40.0,
        embed: Any = None,
    ) -> None:
        self._flash: dict[str, MemoryEntry] = {}
        self._long: dict[str, MemoryEntry] = {}
        self._docs = docworkspace or DocWorkspace()
        self._summarizer = summarizer or deterministic_digest
        self._digest_max = digest_max_chars
        self._large = large_body_chars
        self._cost_unit = cds_cost_unit or 40.0
        # Optional embedder ``embed(text) -> vector`` for SEMANTIC recall (L2). When set, recall /
        # tier / render_index score by L1 lexical + L2 cosine, so a synonym query that shares no
        # tokens with the target still ranks it. None ⇒ lexical-only (byte-identical default).
        self._embed = embed
        self._seq = 0

    def _q_vec(self, query: str | None) -> Any:
        return self._embed(query) if (self._embed is not None and query) else None

    # ── write ─────────────────────────────────────────────────────────────────
    def remember(
        self,
        kind: str,
        content: Any,
        *,
        scope: str = FLASH_SCOPE,
        key: str | None = None,
        digest: str | None = None,
        meta: dict | None = None,
        pinned: bool = False,
        source: str = "",
    ) -> str:
        """Store an entry; return its handle. Large bodies offload to DocWorkspace."""
        self._seq += 1
        meta = dict(meta or {})
        body = _stringify(content)
        key = key or self._auto_key(kind, meta)
        handle = f"mem://{kind}/{scope}/{key}"
        offloaded = len(body) >= self._large
        if offloaded:
            self._docs.offload(handle, body)
        dg = digest if digest is not None else self._summarizer(kind, meta, body)
        dg = self._truncate(dg)
        entry = MemoryEntry(
            handle=handle,
            kind=kind,
            scope=scope,
            digest=dg,
            body=body,
            utility=KIND_UTILITY.get(kind, 1.0),
            tokens=est_tokens(body),
            pinned=pinned,
            recency=float(self._seq),
            source=source,
            meta=meta,
            created_seq=self._seq,
            offloaded=offloaded,
        )
        self._bucket(scope)[handle] = entry
        return handle

    def _auto_key(self, kind: str, meta: dict) -> str:
        if meta.get("key"):
            return _SLUG_RE.sub("-", str(meta["key"]).lower()).strip("-")[:48]
        if meta.get("tool"):
            return f"{_SLUG_RE.sub('-', str(meta['tool']).lower()).strip('-')[:32]}-{self._seq:04d}"
        return f"{self._seq:06d}"

    def _truncate(self, s: str) -> str:
        return s if len(s) <= self._digest_max else s[: self._digest_max] + "…"

    def _bucket(self, scope: str) -> dict[str, MemoryEntry]:
        return self._flash if scope == FLASH_SCOPE else self._long

    # ── read-back ──────────────────────────────────────────────────────────────
    def get(self, handle: str) -> MemoryEntry | None:
        return self._flash.get(handle) or self._long.get(handle)

    def read(self, handle: str) -> str | None:
        """The full body (the detail re-enters context). None if unknown."""
        e = self.get(handle)
        return e.body if e is not None else None

    def grep(self, handle: str, pattern: str, *, max_matches: int = 50) -> list[dict]:
        """Matching lines (not the whole body). Uses DocWorkspace for offloaded bodies."""
        e = self.get(handle)
        if e is None:
            return []
        if e.offloaded:
            return self._docs.grep(handle, pattern, max_matches=max_matches)
        rx = re.compile(pattern, re.IGNORECASE)
        out = []
        for line in e.body.splitlines():
            if rx.search(line):
                out.append({"line": line.strip()[:200]})
                if len(out) >= max_matches:
                    break
        return out

    def read_section(self, handle: str, section: str) -> str | None:
        """One bounded slice of an offloaded (large) body, via DocWorkspace."""
        e = self.get(handle)
        if e is None or not e.offloaded:
            return None
        try:
            return self._docs.read_section(handle, section)
        except KeyError:
            return None

    def outline(self, handle: str) -> list[dict]:
        e = self.get(handle)
        return self._docs.outline(handle) if (e is not None and e.offloaded) else []

    def recall(
        self,
        query: str | None = None,
        *,
        handle: str | None = None,
        kind: str | None = None,
        scope: str | None = None,
        full: bool = False,
        k: int = 8,
    ) -> Any:
        """The universal read. ``handle`` → that entry (body if ``full``); else search/list the
        digest index across both tiers, scored by relevance to ``query`` (newest-first if none)."""
        if handle is not None:
            e = self.get(handle)
            if e is None:
                return None
            return e.body if full else e
        entries = [*self._long.values(), *self._flash.values()]
        if kind is not None:
            entries = [e for e in entries if e.kind == kind]
        if scope is not None:
            entries = [e for e in entries if e.scope == scope]
        if query:
            q_vec = self._q_vec(query)
            for e in entries:
                e.relevance = score_relevance(
                    query, q_vec, f"{e.kind} {e.digest}", embed_one=self._embed
                )["activation"]
            entries = sorted(entries, key=lambda e: (-e.relevance, -e.recency))
        else:
            entries = sorted(entries, key=lambda e: -e.recency)
        return entries[:k]

    # ── value / tiering (the thinking-palette selection) ───────────────────────
    def tier(
        self,
        entries: list[MemoryEntry],
        *,
        query: str,
        budget_tokens: int,
        inject_threshold: float = 0.30,
        hint_threshold: float = 0.12,
    ) -> list[MemoryEntry]:
        """Score entries by CDS vs ``query`` and assign tiers (1 inject · 2 digest+handle ·
        3 offload), greedy under ``budget_tokens``. Pinned entries floor to Tier 1. Mutates +
        returns the entries. Mirrors ``context_builder.route_tiers`` over memory entries."""
        q_vec = self._q_vec(query)
        for e in entries:
            e.relevance = score_relevance(
                query, q_vec, f"{e.kind} {e.digest}", embed_one=self._embed
            )["activation"]
            cost = max(1.0, (e.tokens or est_tokens(e.body)) / self._cost_unit)
            e.cds = (max(0.0, e.relevance) * max(0.0, e.utility)) / cost
            e.tier = 0
        used = 0
        for e in entries:
            if e.pinned:
                e.tier = 1
                used += e.tokens
        for e in sorted((x for x in entries if x.tier == 0), key=lambda x: (-x.cds, -x.recency)):
            if e.cds >= inject_threshold and used + e.tokens <= budget_tokens:
                e.tier = 1
                used += e.tokens
            elif e.cds >= hint_threshold:
                e.tier = 2  # digest + read-back handle (discoverable, never silently dropped)
            else:
                e.tier = 3  # offloaded: handle only
        return entries

    # ── lifecycle ──────────────────────────────────────────────────────────────
    def forget(self, handle: str) -> bool:
        for bucket in (self._flash, self._long):
            if handle in bucket:
                del bucket[handle]
                return True
        return False

    def reset_flash(self) -> None:
        """Drop the turn's working memory (tool results, reasoning temps). Long-term persists."""
        self._flash.clear()

    def reset(self) -> None:
        """Drop ALL state (flash + long-term + offloaded bodies). Used when rebinding a shared
        store to a fresh session before :meth:`load_long` / :meth:`from_json`."""
        self._flash.clear()
        self._long.clear()
        self._docs = DocWorkspace()
        self._seq = 0

    # ── snapshot / restore (Redis offloading) ───────────────────────────────────
    def dump_long(
        self,
        *,
        max_entries: int = _SNAPSHOT_MAX_ENTRIES,
        max_tokens: int = _SNAPSHOT_MAX_TOKENS,
    ) -> list[dict]:
        """The durable tier as a bounded list of loss-free entry snapshots. Flash is NOT
        included — it is turn-scratch (cleared each turn), so it never persists and there is no
        cross-turn race. Pinned entries always survive; the rest are kept highest-CDS-then-newest
        until a cap is hit."""
        pinned = [e for e in self._long.values() if e.pinned]
        rest = sorted(
            (e for e in self._long.values() if not e.pinned),
            key=lambda e: (-e.cds, -e.recency),
        )
        out: list[dict] = []
        used = sum(e.tokens for e in pinned)
        for e in pinned:
            out.append(e.snapshot())
        for e in rest:
            if len(out) >= max_entries:
                break
            if used + e.tokens > max_tokens:
                continue
            out.append(e.snapshot())
            used += e.tokens
        return out

    def load_long(self, entries: list[dict] | None) -> None:
        """Restore durable entries (replacing by handle), advancing the seq counter so new
        writes never collide with restored ones."""
        for d in entries or []:
            e = MemoryEntry.restore(d)
            self._long[e.handle] = e
            if e.created_seq > self._seq:
                self._seq = e.created_seq

    def to_json(
        self,
        *,
        max_entries: int = _SNAPSHOT_MAX_ENTRIES,
        max_tokens: int = _SNAPSHOT_MAX_TOKENS,
    ) -> dict:
        """Full session snapshot: durable tier + the offloaded bodies it references. Flash is
        dropped (turn-scratch). Pair with :meth:`from_json`."""
        long = self.dump_long(max_entries=max_entries, max_tokens=max_tokens)
        kept_offloaded = {e["handle"] for e in long if e.get("offloaded")}
        docs = {h: v for h, v in self._docs.to_json().items() if h in kept_offloaded}
        return {"seq": self._seq, "long": long, "docs": docs}

    @classmethod
    def from_json(cls, data: dict | None, *, embed: Any = None, **kwargs: Any) -> MemoryStore:
        """Rebuild a store from :meth:`to_json`. Flash starts empty; long-term + offloaded bodies
        are restored. ``embed`` and other ctor kwargs configure the fresh store."""
        store = cls(embed=embed, **kwargs)
        store.restore(data)
        return store

    def restore(self, data: dict | None) -> None:
        """Reset this store IN PLACE and load a snapshot (pairs with :meth:`to_json`). In-place so
        a long-lived store the engine already references is re-bound to a new session's memory
        without reconstructing it — the stateless-serving seam."""
        self.reset()
        data = data or {}
        self._docs = DocWorkspace.from_json(data.get("docs"))
        self.load_long(data.get("long"))
        self._seq = max(self._seq, int(data.get("seq", 0)))

    def promote(
        self, handle: str, *, scope: str = "conversation", key: str | None = None
    ) -> str | None:
        """Write a flash entry back to long-term (a fact that proved durable, a concluded
        decision), consolidating against an existing entry with the same key. Returns the new
        long-term handle, or None if the source is unknown."""
        e = self.get(handle)
        if e is None:
            return None
        return self.remember(
            e.kind,
            e.body,
            scope=scope,
            key=key or e.meta.get("key"),
            digest=e.digest,
            meta=e.meta,
            pinned=e.pinned,
            source=e.source or handle,
        )

    # ── funnel integration ─────────────────────────────────────────────────────
    def compaction_summarizer(self):
        """A sync ``summarize(name, input, raw) -> digest`` for ``funnel.compact_observations`` /
        ``tier_observations``: offloads the spent tool body to flash memory and returns a dense
        digest that NAMES the handle, so the compacted result is re-fetchable via ``read``.

        The digest is prefixed with the funnel's ``SPENT_MARKER`` so re-tiering an already-demoted
        observation is a no-op (no duplicate offload) — preserving the funnel's idempotence."""
        from agent_sdk.react.funnel import SPENT_MARKER

        def _summarize(name: str, inp: Any, raw: str) -> str:
            handle = self.remember(
                "tool_result",
                raw,
                scope=FLASH_SCOPE,
                meta={"tool": name, "args": inp},
                source=name,
            )
            digest = self.get(handle).digest  # type: ignore[union-attr]
            return f"{SPENT_MARKER} {digest} · read('{handle}') for full"

        return _summarize

    # ── discoverability: the always-on memory index ────────────────────────────
    def render_index(
        self,
        *,
        query: str | None = None,
        budget_tokens: int = 600,
        max_per_kind: int = 6,
        kinds: tuple[str, ...] | None = None,
    ) -> str:
        """The memory MENU — how the model knows what it has stored, so it can recall correctly.

        One line per entry (``handle — digest``), grouped by kind, newest-first, capped per kind and
        by a token budget; entries that don't fit are announced as a count (``recall(query=…)`` finds
        them). Injected each turn as ``## Memory``. This is the Tier-2 surface over *all* of memory:
        an entry always has at least this one-line presence, so nothing is ever silently invisible.
        """
        # With a query, keep recall's RELEVANCE order (so the menu surfaces what the turn needs, not
        # just the newest); with no query, newest-first. Re-sorting by recency here was a bug: at
        # scale it buried the relevant old entry under recent noise.
        entries = (
            self.recall(query=query, k=10_000)
            if query
            else sorted(self.entries(), key=lambda e: -e.recency)
        )
        by_kind: dict[str, list[MemoryEntry]] = {}
        for e in entries:
            if kinds and e.kind not in kinds:
                continue
            by_kind.setdefault(e.kind, []).append(e)
        header = "## Memory — recall(handle) to expand a digest, recall(query=…) to search"
        lines = [header]
        used = est_tokens(header)
        dropped = 0
        for kind, es in by_kind.items():
            for e in es[:max_per_kind]:
                line = f"- [{kind}] {e.handle} — {e.digest}"
                t = est_tokens(line)
                if used + t > budget_tokens:
                    dropped += 1
                    continue
                lines.append(line)
                used += t
            dropped += max(0, len(es) - max_per_kind)
        if dropped:
            lines.append(f"- (+{dropped} more — recall(query=…) to find them)")
        return "\n".join(lines)

    # ── introspection ──────────────────────────────────────────────────────────
    def stats(self) -> dict:
        return {
            "flash": len(self._flash),
            "long_term": len(self._long),
            "flash_tokens": sum(e.tokens for e in self._flash.values()),
            "long_term_tokens": sum(e.tokens for e in self._long.values()),
        }

    def entries(self, *, scope: str | None = None) -> list[MemoryEntry]:
        all_e = [*self._long.values(), *self._flash.values()]
        return [e for e in all_e if scope is None or e.scope == scope]
