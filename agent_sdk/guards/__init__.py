"""Deterministic answer/output guards — pure leak detectors (no LLM, no I/O).

The mechanism lives here; wire it as a turn post-check via
``agent_sdk.plugins.guardrails.make_answer_leak_check``.
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
]
