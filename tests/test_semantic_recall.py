"""Semantic recall — the embed seam lets recall find a lexically-disjoint synonym match that
lexical-only recall misses. Default (no embedder) stays lexical (byte-identical)."""

from __future__ import annotations

from agent_sdk.memory.universal import MemoryStore
from benchmarks._shared import concept_embed


def _seed(store: MemoryStore) -> str:
    # The target shares a CONCEPT with the query ("schedule") but ZERO content tokens.
    target = store.remember("fact", "the rollout is scheduled for Friday", key="target")
    # A lexical decoy: shares query tokens ("when will the") but no concept.
    store.remember("note", "when will the the the meeting notes", key="decoy")
    # Unrelated distractors (other concepts).
    for i in range(50):
        store.remember("note", f"the invoice payment cost budget item {i}", key=f"money{i}")
    return target


def test_semantic_recall_finds_lexically_disjoint_match():
    store = MemoryStore(embed=concept_embed)
    target = _seed(store)
    top = store.recall(query="when will the launch happen", k=1)
    assert top and top[0].handle == target  # "launch" ~ "rollout/scheduled" via the schedule concept


def test_lexical_only_misses_the_synonym_target():
    store = MemoryStore()  # no embedder → lexical only
    target = _seed(store)
    top = store.recall(query="when will the launch happen", k=1)
    # Lexical recall ranks the token-overlap decoy above the semantically-correct target.
    assert not top or top[0].handle != target


def test_embed_none_is_lexical_byte_identical():
    a = MemoryStore()
    b = MemoryStore(embed=None)
    for s in (a, b):
        s.remember("fact", "the deadline is 2026-07-15", key="d")
    qa = [e.handle for e in a.recall(query="deadline")]
    qb = [e.handle for e in b.recall(query="deadline")]
    assert qa == qb  # embed=None changes nothing
