"""Declarative signal grammar (porting.md §3)."""

from __future__ import annotations

import pytest

from agent_sdk.signals import SignalError, compile_signal, eval_signal


def test_const_and_scalar():
    assert eval_signal({"const": 0.7}, {}) == 0.7
    assert eval_signal(1.0, {}) == 1.0
    assert eval_signal(0, {}) == 0.0
    assert eval_signal(True, {}) == 1.0
    assert eval_signal(None, {}) == 0.0


def test_const_clamps():
    assert eval_signal({"const": 5.0}, {}) == 1.0
    assert eval_signal(-3, {}) == 0.0


def test_flag():
    s = compile_signal({"flag": "is_question"})
    assert s({"is_question": True}) == 1.0
    assert s({"is_question": False}) == 0.0
    assert s({}) == 0.0


def test_lexical_case_insensitive():
    s = compile_signal({"lexical": ["compare", "vs"]})
    assert s({"query": "Compare A and B"}) == 1.0
    assert s({"query": "A vs B"}) == 1.0
    assert s({"query": "hello there"}) == 0.0


def test_min_words_uses_context_or_query():
    s = compile_signal({"min_words": 4})
    assert s({"query": "one two three four"}) == 1.0
    assert s({"query": "one two"}) == 0.0
    assert s({"word_count": 9}) == 1.0


def test_regex():
    s = compile_signal({"regex": r"\?$"})
    assert s({"query": "what is this?"}) == 1.0
    assert s({"query": "a statement"}) == 0.0


def test_all_is_min():
    s = compile_signal({"all": [{"const": 0.4}, {"const": 0.9}]})
    assert s({}) == 0.4


def test_any_is_max():
    s = compile_signal({"any": [{"const": 0.4}, {"const": 0.9}]})
    assert s({}) == 0.9


def test_not():
    s = compile_signal({"not": {"flag": "x"}})
    assert s({"x": True}) == 0.0
    assert s({"x": False}) == 1.0


def test_scale():
    s = compile_signal({"scale": [{"const": 1.0}, 0.6]})
    assert s({}) == pytest.approx(0.6)


def test_sum_clamped():
    s = compile_signal({"sum": [{"const": 0.7}, {"const": 0.8}]})
    assert s({}) == 1.0


def test_nested_composition():
    expr = {"all": [{"flag": "is_question"}, {"any": [{"lexical": ["x"]}, {"min_words": 3}]}]}
    s = compile_signal(expr)
    assert s({"is_question": True, "query": "a b c"}) == 1.0
    assert s({"is_question": True, "query": "short"}) == 0.0
    assert s({"is_question": False, "query": "a b c"}) == 0.0


def test_unknown_operator_raises():
    with pytest.raises(SignalError):
        compile_signal({"frobnicate": 1})


def test_malformed_raises():
    with pytest.raises(SignalError):
        compile_signal({"a": 1, "b": 2})
    with pytest.raises(SignalError):
        compile_signal({"scale": [1.0]})
