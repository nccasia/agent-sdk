"""Golden-answer head — an in-memory cosine index over curated Q→A cases.

A "golden" case pairs a canonical question with its approved answer. The head
embeds the questions once (via an injected ``embed_fn``) and, given a query
vector, finds the nearest case — the substrate for a known-answer short-circuit
(see ``agent_sdk.guards.refusal.golden_hit`` / ``make_pre_turn_gate``).

Distinct from ``SemanticCache`` (which keys exact query-embedding → cached
*result*): this is a *curated*, fuzzy-matched answer head a host populates from
its own source (a golden-set table, a FAQ, an SOP). Pure of any host package —
numpy only; the embedder and the rows are injected.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

__all__ = ["GoldenItem", "GoldenHead"]


@dataclass
class GoldenItem:
    case_id: str
    query: str
    expected_behavior: str
    criteria: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


class GoldenHead:
    """In-memory cosine-similarity index over golden cases for one agent/bot."""

    def __init__(
        self,
        items: list[GoldenItem],
        embeddings: np.ndarray,
        embedding_model_id: str,
    ) -> None:
        self.items = items
        self.embeddings = embeddings  # (n, dim), L2-normalized
        self.embedding_model_id = embedding_model_id

    @classmethod
    def from_raw(
        cls,
        rows: list[dict],
        embed_fn: Callable[[list[str]], Any],
        embedding_model_id: str,
    ) -> GoldenHead | None:
        """Build a head from raw rows (``{case_id|id, query, expected_behavior,
        criteria, tags}``), embedding the queries with ``embed_fn``. None when no
        row carries a query."""
        if not rows:
            return None
        items: list[GoldenItem] = []
        queries: list[str] = []
        for r in rows:
            query = (r.get("query") or "").strip()
            if not query:
                continue
            items.append(
                GoldenItem(
                    case_id=str(r.get("case_id") or r.get("id") or ""),
                    query=query,
                    expected_behavior=r.get("expected_behavior") or "",
                    criteria=list(r.get("criteria") or []),
                    tags=list(r.get("tags") or []),
                )
            )
            queries.append(query)
        if not queries:
            return None
        embeddings = embed_fn(queries)
        if isinstance(embeddings, list):
            embeddings = np.array(embeddings)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        embeddings = embeddings / norms
        logger.info("Built golden head with %d items", len(items))
        return cls(items=items, embeddings=embeddings, embedding_model_id=embedding_model_id)

    @classmethod
    def from_cached_entries(
        cls,
        entries: list[dict],
        embedding_model_id: str,
    ) -> GoldenHead | None:
        """Rebuild a head from a cached bundle (each entry carries its precomputed
        ``embedding``) without re-embedding."""
        items: list[GoldenItem] = []
        vecs: list[list[float]] = []
        for e in entries:
            vec = e.get("embedding")
            query = (e.get("query") or "").strip()
            if not query or not vec:
                continue
            items.append(
                GoldenItem(
                    case_id=str(e.get("case_id") or ""),
                    query=query,
                    expected_behavior=e.get("expected_behavior") or "",
                    criteria=list(e.get("criteria") or []),
                    tags=list(e.get("tags") or []),
                )
            )
            vecs.append(vec)
        if not items:
            return None
        embeddings = np.asarray(vecs, dtype=np.float32)
        return cls(items=items, embeddings=embeddings, embedding_model_id=embedding_model_id)

    def to_cache_bundle(self) -> dict:
        """A JSON-safe bundle (rows + precomputed embeddings) for a host cache."""
        entries = []
        for item, vec in zip(self.items, self.embeddings, strict=True):
            entries.append(
                {
                    "case_id": item.case_id,
                    "query": item.query,
                    "expected_behavior": item.expected_behavior,
                    "criteria": item.criteria,
                    "tags": item.tags,
                    "embedding": vec.astype(float).tolist(),
                }
            )
        return {
            "model_id": self.embedding_model_id,
            "dim": int(self.embeddings.shape[1]) if self.embeddings.size else 0,
            "entries": entries,
        }

    @property
    def size(self) -> int:
        return len(self.items)
