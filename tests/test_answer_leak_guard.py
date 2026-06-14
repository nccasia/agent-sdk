"""Answer-leak guard detectors + the guardrails post-check factory."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent_sdk.guards import (
    answer_leak_violation,
    commitment_violation,
    has_refusal_marker,
    secret_violation,
)
from agent_sdk.plugins.guardrails import GuardrailError, make_answer_leak_check


@dataclass
class _Result:
    text: str


def test_secret_shaped_string_blocked():
    assert secret_violation("here it is: sk-ABCDEF0123456789ghij") == "secret_shaped_string"
    assert answer_leak_violation("token = aB3xZ9kLmN0pQ7rS2tU5") == "secret_shaped_string"


def test_clean_prose_is_allowed():
    assert answer_leak_violation("The capital of France is Paris.") is None
    assert secret_violation("a perfectly normal sentence") is None


def test_bulk_pii_emails_blocked():
    dump = "a@x.com b@x.com c@x.com"
    assert answer_leak_violation(dump) == "bulk_pii_emails"
    # under threshold → allowed
    assert answer_leak_violation("reach me at a@x.com") is None


def test_forbidden_substring_blocked():
    assert answer_leak_violation("this is internal-only data", forbidden=["internal-only"]) == (
        "forbidden:internal-only"
    )


def test_commitment_to_impossible_action():
    # committed → violation
    assert commitment_violation("Sure, I will delete the account now", ["delete the account"])
    # negated mention → safe
    assert commitment_violation("I cannot delete the account", ["delete the account"]) is None
    # no declared actions → no-op
    assert commitment_violation("I will delete the account", []) is None


def test_injectable_cues_other_language():
    # English cues miss a non-English commitment; injected cues catch it
    text = "rồi, mình sẽ xoá tài khoản"
    actions = ["xoá tài khoản"]
    assert commitment_violation(text, actions) is None
    assert commitment_violation(text, actions, commitment_cues=["mình sẽ"]) is not None


def test_has_refusal_marker():
    assert has_refusal_marker("Sorry, I can't help with that")
    assert not has_refusal_marker("Sure, here you go")
    assert has_refusal_marker("mình không thể", markers=["không thể"])


def test_post_check_factory_raises_on_leak():
    check = make_answer_leak_check(forbidden=["internal-only"])
    check(_Result(text="all good"))  # no raise
    with pytest.raises(GuardrailError):
        check(_Result(text="this is internal-only"))


def test_post_check_factory_blocks_impossible_commitment():
    check = make_answer_leak_check(impossible_actions=["change the grade"])
    with pytest.raises(GuardrailError):
        check(_Result(text="Okay, I will change the grade for you"))
    check(_Result(text="I cannot change the grade"))  # negated → allowed
