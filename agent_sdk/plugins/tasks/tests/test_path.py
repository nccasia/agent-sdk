"""Unit — the task recognizer, in isolation (no agent/engine)."""

from __future__ import annotations

from agent_sdk.plugins.tasks.path import recognize


def test_fired_prompt_is_certain():
    assert recognize({"fired_prompt": True}) == 1.0


def test_analytical_cues_score_above_qna():
    for q in [
        "compute the total revenue",
        "what are the top 3 products",
        "how many orders shipped",
        "list customers by spend",
        "average order value per region",
    ]:
        assert recognize({"query": q}) == 0.9, q


def test_plain_questions_and_chitchat_do_not_trigger():
    for q in ["what is the capital of France?", "who are you?", "hello there", "thanks!"]:
        assert recognize({"query": q}) == 0.0, q
