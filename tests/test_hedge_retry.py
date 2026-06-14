"""Anti-hedge answer-retry builder (agent_sdk.react.hedge)."""

from __future__ import annotations

from agent_sdk.react import DEFAULT_HEDGE_DIRECTIVE, make_hedge_retry


def test_hedge_opening_triggers_directive():
    retry = make_hedge_retry()
    assert retry("Sorry, I couldn't find specifics on that.") == DEFAULT_HEDGE_DIRECTIVE
    assert retry("Unfortunately I don't have that information.") == DEFAULT_HEDGE_DIRECTIVE


def test_direct_answer_does_not_retry():
    retry = make_hedge_retry()
    assert retry("The deadline is March 1, per [c12].") is None
    assert retry("") is None


def test_only_checks_the_opening():
    retry = make_hedge_retry()
    # a hedge phrase deep in the body (past 160 chars) does not trigger
    body = "The policy states X. " * 20 + "sorry"
    assert retry(body) is None


def test_custom_markers_and_directive():
    retry = make_hedge_retry(markers=["rất tiếc", "chưa tìm thấy"], directive="TRA_LOI_TRUC_TIEP")
    assert retry("Rất tiếc, mình chưa tìm thấy thông tin.") == "TRA_LOI_TRUC_TIEP"
    assert retry("Sorry, no info") is None  # English default marker not in the custom set
