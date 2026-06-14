"""Deterministic guards — pure, no LLM, no I/O.

- **answer_guard** — output-side leak detectors; wire as a turn post-check via
  ``agent_sdk.plugins.guardrails.make_answer_leak_check``.
- **refusal** — input-side ``pre_turn_gate`` builder (refusal rules + golden
  known-answer short-circuit + semantic refusal), dependency-injected.
"""

from __future__ import annotations

from agent_sdk.guards.answer_guard import (
    BULK_PII_THRESHOLD,
    DEFAULT_COMMITMENT_CUES,
    DEFAULT_NEGATION_CUES,
    DEFAULT_REFUSAL_MARKERS,
    answer_leak_violation,
    bulk_pii_violation,
    commitment_violation,
    forbidden_violation,
    has_refusal_marker,
    secret_violation,
)
from agent_sdk.guards.refusal import (
    golden_hit,
    make_pre_turn_gate,
    make_semantic_refusal,
    match_refusal,
)

__all__ = [
    "BULK_PII_THRESHOLD",
    "DEFAULT_COMMITMENT_CUES",
    "DEFAULT_NEGATION_CUES",
    "DEFAULT_REFUSAL_MARKERS",
    "answer_leak_violation",
    "bulk_pii_violation",
    "commitment_violation",
    "forbidden_violation",
    "has_refusal_marker",
    "secret_violation",
    # input-side pre-turn gate
    "make_pre_turn_gate",
    "make_semantic_refusal",
    "match_refusal",
    "golden_hit",
]
