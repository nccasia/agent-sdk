"""Public metacognition controller."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

from agent_sdk.inspection import EngineSnapshot, FlowAxisSnapshot, LobeAxisSnapshot
from agent_sdk.metacognition.model import MetaAction, MetaDecision
from agent_sdk.metacognition.monitor import monitor
from agent_sdk.metacognition.regulator import regulate

# Metacognition is ALWAYS ON (ENGINE 0.7.1) — there is no "disabled" mode.
# "observe" is the floor: monitor + trace every decision, never mutate
# execution. "apply" additionally applies the allow-listed actions. Legacy
# off-tokens (off/0/false/disabled) map to "observe": the mutation kill
# switch survives, the observability never turns off.
MetaMode = Literal["observe", "apply"]

_OBSERVE = {
    "observe",
    "observability",
    "shadow",
    "dry_run",
    "dry-run",
    "log",
    # Legacy "disabled" vocabulary — maps to observe (monitor-only).
    "off",
    "0",
    "false",
    "disabled",
    "disable",
}
_APPLY = {"on", "1", "true", "enabled", "enable", "apply"}
# Actions the interpreter actually implements an apply seam for
# (``_run_pipeline``): adjust mutates the step's lobe slice, skip drops the
# step, retry re-runs a failed/empty step ONCE (post-run, regulator-decided).
# ``meta_review`` has no apply seam — observe-only by construction and
# cannot be enabled via policy.
_APPLY_CAPABLE_ACTIONS: frozenset[MetaAction] = frozenset(
    {"adjust_lobe_slice", "skip_step", "retry_step"}
)
# Production default: trim-only. Widening to ``skip_step``/``retry_step`` is
# a per-policy opt-in via ``metacognition_apply_actions`` — a deliberate
# rollout decision.
_DEFAULT_APPLY_ACTIONS: frozenset[MetaAction] = frozenset({"adjust_lobe_slice"})


def _normalize_mode(value: object) -> MetaMode | None:
    if value is None:
        return None
    raw = str(value).strip().lower()
    if raw in _OBSERVE:
        return "observe"
    if raw in _APPLY:
        return "apply"
    return None


def metacognition_mode(policy: Mapping | None = None) -> MetaMode:
    env_mode = _normalize_mode(os.environ.get("METACOGNITION"))
    if env_mode is not None:
        return env_mode
    policy = policy or {}
    policy_mode = _normalize_mode(policy.get("metacognition_mode"))
    if policy_mode is not None:
        return policy_mode
    # Back-compat: the original boolean flag meant enabled/apply; the False
    # half now means observe — monitoring never turns off.
    if "metacognition_enabled" in policy:
        return "apply" if bool(policy.get("metacognition_enabled")) else "observe"
    return "apply"


def metacognition_enabled(policy: Mapping | None = None) -> bool:
    """Always True since ENGINE 0.7.1 — metacognition cannot be turned off.

    Kept for back-compat callers; the meaningful question is now
    ``metacognition_mode(policy)`` (observe vs apply).
    """
    return True


def _allowed_actions(policy: Mapping | None = None) -> frozenset[MetaAction]:
    raw = (policy or {}).get("metacognition_apply_actions")
    if raw is None:
        return _DEFAULT_APPLY_ACTIONS
    if isinstance(raw, str):
        items = [part.strip() for part in raw.split(",")]
    elif isinstance(raw, (list, tuple, set, frozenset)):
        items = [str(part).strip() for part in raw]
    else:
        return _DEFAULT_APPLY_ACTIONS
    allowed = {item for item in items if item in _APPLY_CAPABLE_ACTIONS}
    return frozenset(allowed)


class MetaController:
    """Monitor and regulate object-level OX/OY thinking."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        mode: MetaMode | None = None,
        apply_actions: frozenset[MetaAction] | None = None,
    ):
        if mode is None:
            # Legacy boolean: True/None → apply; False → observe (the floor —
            # metacognition cannot be turned off, only kept from mutating).
            mode = "observe" if enabled is False else "apply"
        self.mode: MetaMode = mode
        self.enabled = True  # always — kept for back-compat callers
        self.apply_actions = _DEFAULT_APPLY_ACTIONS if apply_actions is None else apply_actions

    @classmethod
    def from_policy(cls, policy: Mapping | None = None) -> MetaController:
        return cls(mode=metacognition_mode(policy), apply_actions=_allowed_actions(policy))

    def should_apply(self, action: MetaAction) -> bool:
        return self.mode == "apply" and action in self.apply_actions

    def plan_next(
        self,
        *,
        lobe_axis: LobeAxisSnapshot | None = None,
        flow_axis: FlowAxisSnapshot | None = None,
        engine: EngineSnapshot | None = None,
        target_flow: str | None = None,
        target_step: str | None = None,
        current_lobes: tuple[str, ...] = (),
    ) -> MetaDecision:
        observations = monitor(lobe_axis=lobe_axis, flow_axis=flow_axis, engine=engine)
        return regulate(
            observations,
            target_flow=target_flow,
            target_step=target_step,
            current_lobes=current_lobes,
        )
