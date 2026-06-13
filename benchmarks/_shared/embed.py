"""A deterministic CONCEPT embedder for the memory bench + tests.

Real semantic recall needs an embedding model the SDK leaf can't import. To prove the semantic-recall
seam reproducibly (no network, no model), this maps text to a vector over a fixed *concept* vocabulary:
each concept is a synonym set, and a text's vector lights the dimensions of the concepts its tokens
hit, L2-normalized. Two texts that share a concept — even with **zero token overlap** ("deadline" vs
"due date") — then have cosine ≈ 1, while unrelated texts have cosine 0. That is exactly the semantic
signal lexical recall lacks, made deterministic.

The bench's `recall_curve` mode uses this to show semantic recall holding while lexical collapses. The
`--live` tier may swap a real embedder.
"""

from __future__ import annotations

import re

import numpy as np

# Concept → synonym tokens. Synonyms across a row share a dimension (so they match semantically);
# tokens are single words (the embedder tokenizes on word boundaries).
CONCEPTS: dict[str, set[str]] = {
    "deadline": {"deadline", "duedate", "cutoff", "due", "eta", "deliver"},
    "schedule": {"schedule", "scheduled", "rollout", "launch", "planned", "timing", "ship"},
    "money": {"cost", "price", "budget", "payment", "invoice", "billing", "charge"},
    "incident": {"incident", "outage", "failure", "downtime", "breach", "alert"},
    "owner": {"owner", "assignee", "responsible", "oncall", "lead", "maintainer"},
    "location": {"location", "venue", "room", "place", "address", "where"},
    "preference": {"preference", "prefers", "likes", "wants", "setting", "config"},
    "security": {"security", "auth", "permission", "access", "credential", "token"},
    "performance": {"performance", "latency", "slow", "throughput", "speed", "perf"},
    "data": {"data", "database", "table", "record", "query", "index", "schema"},
}
_CONCEPT_LIST = list(CONCEPTS)
_TOKEN_OF: dict[str, int] = {tok: i for i, (_c, syns) in enumerate(CONCEPTS.items()) for tok in syns}
_DIM = len(_CONCEPT_LIST)

_WORD = re.compile(r"[a-z0-9]+")


def concept_embed(text: str) -> np.ndarray:
    """Text → L2-normalized concept vector. Deterministic. Zero vector when no concept hits (so
    unrelated texts score 0 cosine, not a false match)."""
    vec = np.zeros(_DIM, dtype=np.float32)
    for tok in _WORD.findall((text or "").lower()):
        i = _TOKEN_OF.get(tok)
        if i is not None:
            vec[i] += 1.0
    n = float(np.linalg.norm(vec))
    return vec / n if n else vec


def concept_of(word: str) -> str | None:
    i = _TOKEN_OF.get(word.lower())
    return _CONCEPT_LIST[i] if i is not None else None


__all__ = ["concept_embed", "concept_of", "CONCEPTS"]
