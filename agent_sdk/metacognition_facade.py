"""``Metacognition`` — the first-class, subclassable reasoning-control object.

It monitors the object-level state and regulates the next step within an
allow-list. ``cite`` / ``filter`` stay pinned and are never skippable regardless
of a custom subclass — the engine enforces it, not the metacognition object.

    Metacognition(mode="apply", apply_actions={"adjust_lobe_slice"})

    class DomainMeta(Metacognition):
        def monitor(self, snapshot) -> list[Observation]: ...
        def regulate(self, observations, *, stage, lobes) -> Decision: ...
"""

from __future__ import annotations

from typing import Any

from agent_sdk.metacognition.controller import _APPLY_CAPABLE_ACTIONS, MetaController
from agent_sdk.metacognition.model import MetaDecision

__all__ = ["Metacognition", "PINNED_UNSKIPPABLE"]

# The engine never lets metacognition skip these, regardless of subclass.
PINNED_UNSKIPPABLE = frozenset({"cite", "filter"})


class Metacognition:
    """Reasoning controller. Strings ``"apply"`` / ``"observe"`` are accepted as
    shorthand wherever a ``Metacognition`` is expected (see :meth:`coerce`)."""

    def __init__(
        self, mode: str = "apply", *, apply_actions: set[str] | frozenset[str] | None = None
    ):
        if mode not in ("apply", "observe"):
            raise ValueError("mode must be 'apply' or 'observe'")
        self.mode = mode
        actions = apply_actions if apply_actions is not None else {"adjust_lobe_slice"}
        self.apply_actions = frozenset(a for a in actions if a in _APPLY_CAPABLE_ACTIONS)
        self._controller = MetaController(mode=mode, apply_actions=self.apply_actions)

    @classmethod
    def coerce(cls, value: Any) -> Metacognition:
        if value is None:
            return cls(mode="observe")
        if isinstance(value, Metacognition):
            return value
        if isinstance(value, str):
            return cls(mode=value)
        raise TypeError(f"cannot coerce {value!r} to Metacognition")

    def should_apply(self, action: str) -> bool:
        return self._controller.should_apply(action)

    def plan_next(self, **kwargs: Any) -> MetaDecision:
        """Run monitor → regulate, then enforce the pinned-unskippable guard."""
        decision = self._controller.plan_next(**kwargs)
        target_step = kwargs.get("target_step")
        if decision.action == "skip_step" and str(target_step) in PINNED_UNSKIPPABLE:
            # The engine's structural guarantee: cite/filter are never skipped.
            return MetaDecision(
                action="continue", reason="pinned:never_skip", target_step=target_step
            )
        return decision

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"Metacognition(mode={self.mode!r}, apply_actions={set(self.apply_actions)})"
