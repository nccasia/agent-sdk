"""Declarative per-stage overrides + grounded-temp invariant."""

from __future__ import annotations

import pytest

from agent_sdk.stage_overrides import (
    GROUNDED_STAGES,
    apply_stage_overrides,
    assert_grounded_stages_zero_temp,
)
from agent_sdk.stages import Stage


def _stages():
    return [
        Stage("qna:research", name="research", loop="agentic", temperature=0.4, fanout_parallel=True),
        Stage("qna:synthesize", name="synthesize", temperature=0.0),
        Stage("qna:cite", name="cite", temperature=0.0),
    ]


def test_no_overrides_is_passthrough():
    stages = _stages()
    out = apply_stage_overrides(stages, None)
    assert [s.id for s in out] == [s.id for s in stages]


def test_bare_name_override_applies_to_namespaced_stage():
    out = apply_stage_overrides(_stages(), {"research": {"system_prompt": "be terse", "max_tokens": 99}})
    research = next(s for s in out if s.id == "qna:research")
    assert research.system_prompt == "be terse"
    assert research.max_tokens == 99


def test_override_preserves_unlisted_fields():
    # the research stage has fanout_parallel=True — an override must NOT drop it
    out = apply_stage_overrides(_stages(), {"research": {"system_prompt": "x"}})
    research = next(s for s in out if s.id == "qna:research")
    assert research.fanout_parallel is True
    assert research.loop == "agentic"


def test_budget_hops_and_loop_override():
    out = apply_stage_overrides(_stages(), {"research": {"loop": "single", "budget": {"hops": 3}}})
    research = next(s for s in out if s.id == "qna:research")
    assert research.loop == "single"
    assert research.hops == 3


def test_grounded_stage_invariant_raises_on_breach():
    bad = [Stage("qna:synthesize", name="synthesize", temperature=0.7)]
    with pytest.raises(AssertionError):
        assert_grounded_stages_zero_temp(bad)


def test_override_cannot_break_grounded_invariant():
    with pytest.raises(AssertionError):
        apply_stage_overrides(_stages(), {"synthesize": {"temperature": 0.9}})


def test_grounded_stages_pass_at_zero():
    assert_grounded_stages_zero_temp(_stages())  # no raise
    assert GROUNDED_STAGES == ("synthesize", "cite", "filter")
