"""Declarative per-stage overrides + the grounded-stage temperature invariant.

A host tunes the built-in network per stage from config — a
``{stage_name: {system_prompt?, temperature?, max_tokens?, loop?, budget:{hops?}}}``
dict — without re-authoring stages in code. ``apply_stage_overrides`` patches the
matching production :class:`~agent_sdk.stages.Stage` objects; unset knobs keep the
production default. Matching is the same rule skills use: an exact stage-id match,
else the bare logical-step suffix (``qna:synthesize`` ← ``"synthesize"``), so one
override tunes every flow's step of that name.

``assert_grounded_stages_zero_temp`` enforces the SDK's grounding invariant —
``synthesize`` / ``cite`` / ``filter`` must run at ``temperature == 0`` — and is
re-asserted after patching so an override can never break it.

Stages are cloned with :func:`copy.copy` (preserving every field, including ones
not enumerated here) so an override never silently drops a stage attribute.
"""

from __future__ import annotations

import copy
from typing import Any

from agent_sdk.stages import Stage

__all__ = ["apply_stage_overrides", "assert_grounded_stages_zero_temp", "GROUNDED_STAGES"]

GROUNDED_STAGES = ("synthesize", "cite", "filter")

_LOOPS = ("none", "single", "agentic", "map")


def assert_grounded_stages_zero_temp(stages: Any) -> None:
    """Hard invariant: synthesize/cite/filter run at temperature 0. Raises on breach."""
    for s in stages:
        name = getattr(s, "name", "") or getattr(s, "id", "")
        bare = str(name).rsplit(":", 1)[-1]
        if bare in GROUNDED_STAGES and getattr(s, "temperature", None) not in (None, 0, 0.0):
            raise AssertionError(f"stage {name!r} must be temperature==0")


def _override_for(stage_id: str, overrides: dict) -> dict | None:
    """The override config for a stage id — exact full-id match, else bare-suffix
    match (``qna:synthesize`` → ``overrides["synthesize"]``)."""
    cfg = overrides.get(stage_id)
    if cfg is None:
        cfg = overrides.get(stage_id.rsplit(":", 1)[-1])
    return cfg if isinstance(cfg, dict) else None


def _patch(st: Stage, cfg: dict) -> Stage:
    """Clone ``st`` (all fields preserved) with the honored overrides applied."""
    new = copy.copy(st)
    budget = cfg.get("budget") if isinstance(cfg.get("budget"), dict) else {}
    if system_prompt := cfg.get("system_prompt"):
        new.system_prompt = system_prompt
    if (temperature := cfg.get("temperature")) is not None:
        new.temperature = temperature
    if (max_tokens := cfg.get("max_tokens")) is not None:
        new.max_tokens = max_tokens
    if (hops := budget.get("hops")) is not None:
        new.hops = hops
    if cfg.get("loop") in _LOOPS:
        new.loop = cfg["loop"]
    return new


def apply_stage_overrides(stages: list[Stage], overrides: Any) -> list[Stage]:
    """Return ``stages`` patched from an ``overrides`` dict
    (``{stage_name: {system_prompt?, temperature?, max_tokens?, loop?, budget:{hops?}}}``).
    No-op (a fresh list) when there are no overrides. Re-asserts the grounded-stage
    zero-temperature invariant.

    ``model`` is intentionally not applied here — per-stage model dispatch is an
    engine/client concern, not a stage-clone concern.
    """
    if not isinstance(overrides, dict) or not overrides:
        return list(stages)
    out = [_patch(st, cfg) if (cfg := _override_for(st.id, overrides)) else st for st in stages]
    assert_grounded_stages_zero_temp(out)
    return out
