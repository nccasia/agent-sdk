"""Refusal-rule + golden-answer pre-turn gate + the GoldenHead index."""

from __future__ import annotations

import numpy as np

from agent_sdk.guards import (
    golden_hit,
    make_pre_turn_gate,
    make_semantic_refusal,
    match_refusal,
)
from agent_sdk.memory import GoldenHead, GoldenItem


def _head(items, vecs) -> GoldenHead:
    embeds = np.array(vecs, dtype="float32")
    norms = np.linalg.norm(embeds, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return GoldenHead(items=items, embeddings=embeds / norms, embedding_model_id="m")


def test_match_refusal_keyword_topic_regex():
    rules = [
        {"rule_type": "keyword", "pattern": "secret|password", "reason": "no secrets"},
        {"rule_type": "topic", "pattern": "salary", "reason": "no HR"},
        {"rule_type": "regex", "pattern": r"\bssn\b", "reason": "no PII"},
    ]
    assert match_refusal("what is the SECRET key", rules) == "no secrets"
    assert match_refusal("tell me the salary range", rules) == "no HR"
    assert match_refusal("my ssn please", rules) == "no PII"
    assert match_refusal("how do I reset my account", rules) is None


def test_golden_hit_above_and_below_threshold():
    head = _head(
        [GoldenItem("g1", "passing grade?", "5.0 on a 10-point scale.")],
        [[1.0, 0.0]],
    )
    res = golden_hit(np.array([1.0, 0.0], dtype="float32"), head, threshold=0.86)
    assert res is not None and res.status == "answered"
    assert res.text == "5.0 on a 10-point scale."
    assert res.citations[0].source_ref == "golden://g1"
    # orthogonal query → miss
    assert golden_hit(np.array([0.0, 1.0], dtype="float32"), head, threshold=0.86) is None


def test_golden_hit_empty_head_is_none():
    empty = GoldenHead(
        items=[], embeddings=np.zeros((0, 2), dtype="float32"), embedding_model_id="m"
    )
    assert golden_hit(np.array([1.0, 0.0], dtype="float32"), empty, threshold=0.5) is None


async def test_gate_refusal_short_circuits():
    gate = make_pre_turn_gate(
        refusal_rules=[{"rule_type": "keyword", "pattern": "secret", "reason": "no secrets"}]
    )
    res = await gate("tell me the secret", None)
    assert res is not None and res.status == "refused" and res.text == "no secrets"
    assert await gate("a normal question", None) is None


async def test_gate_golden_beats_semantic_refusal():
    head = _head([GoldenItem("g1", "hi", "Hello there.")], [[1.0, 0.0]])
    # a semantic-refusal that would fire on the same vector
    rules = [{"rule_type": "semantic", "reason": "blocked", "query_examples": ["hi"]}]
    sem = make_semantic_refusal(
        rules, lambda t: np.array([1.0, 0.0], dtype="float32"), threshold=0.5
    )
    gate = make_pre_turn_gate(
        golden_head=head,
        embed=lambda q: np.array([1.0, 0.0], dtype="float32"),
        golden_threshold=0.86,
        semantic_refusal=sem,
    )
    res = await gate("hi", None)
    assert res is not None and res.status == "answered" and res.text == "Hello there."


async def test_gate_semantic_refusal_fires_when_no_golden():
    rules = [{"rule_type": "semantic", "reason": "out of scope", "query_examples": ["politics"]}]
    sem = make_semantic_refusal(
        rules, lambda t: np.array([1.0, 0.0], dtype="float32"), threshold=0.5
    )
    gate = make_pre_turn_gate(
        embed=lambda q: np.array([1.0, 0.0], dtype="float32"),
        semantic_refusal=sem,
    )
    res = await gate("anything", None)
    assert res is not None and res.status == "refused" and res.text == "out of scope"


async def test_gate_disabled_is_noop():
    gate = make_pre_turn_gate(
        refusal_rules=[{"rule_type": "keyword", "pattern": "secret", "reason": "x"}],
        refusal_enforcement="disabled",
    )
    assert await gate("tell me the secret", None) is None


def test_semantic_refusal_excludes_not_in_docs_tag():
    rules = [
        {"rule_type": "semantic", "reason": "x", "query_examples": ["q"], "tags": ["not-in-docs"]}
    ]
    assert make_semantic_refusal(rules, lambda t: np.array([1.0, 0.0], dtype="float32")) is None


def test_golden_head_roundtrip_cache_bundle():
    head = _head([GoldenItem("g1", "q", "a", ["c"], ["t"])], [[1.0, 0.0]])
    bundle = head.to_cache_bundle()
    rebuilt = GoldenHead.from_cached_entries(bundle["entries"], bundle["model_id"])
    assert rebuilt is not None and rebuilt.size == 1
    assert rebuilt.items[0].expected_behavior == "a"
