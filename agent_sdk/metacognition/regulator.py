"""Metacognitive regulation: choose what to think about next."""

from __future__ import annotations

from agent_sdk.contracts.pins import PINNED_LOBES
from agent_sdk.metacognition.model import MetaDecision, MetaObservation, MetaQueueItem

_TRIMMABLE_LOBES = {
    "skill_select",
    "skill_active",
    "memory_recall",
    "session_recall",
    "ctxvar_resolve",
}

# The cite/filter stage names coincide with their pinned lobe ids — the
# ground-or-refuse contract. A pinned step is never skippable: an empty
# lobe slice there is a config error that needs review, not a skip.
_PINNED_STEPS = PINNED_LOBES


def _split_target(target: str) -> tuple[str | None, str | None]:
    if "." not in target:
        return None, None
    flow, step = target.split(".", 1)
    return flow or None, step or None


def regulate(
    observations: tuple[MetaObservation, ...],
    *,
    target_flow: str | None = None,
    target_step: str | None = None,
    current_lobes: tuple[str, ...] = (),
) -> MetaDecision:
    """Turn observations into a next-thinking decision."""
    if not observations:
        return MetaDecision(
            action="continue",
            target_flow=target_flow,
            target_step=target_step,
            target_lobes=current_lobes,
            reason="object-level state is healthy",
            confidence=1.0,
        )

    queue = tuple(
        MetaQueueItem(target=obs.target, reason=obs.detail or obs.kind, priority=obs.severity)
        for obs in sorted(observations, key=lambda obs: (-obs.severity, obs.id))
    )
    by_kind = {obs.kind: obs for obs in observations}

    if obs := by_kind.get("low_confidence_path"):
        return MetaDecision(
            action="meta_review",
            target_flow=target_flow,
            target_step=target_step,
            target_lobes=current_lobes,
            reason=obs.detail,
            confidence=0.45,
            queue=queue,
            observations=observations,
        )

    if obs := by_kind.get("context_tight"):
        flow, step = _split_target(obs.target)
        narrowed = tuple(lobe for lobe in current_lobes if lobe not in _TRIMMABLE_LOBES)
        if narrowed and narrowed != current_lobes:
            return MetaDecision(
                action="adjust_lobe_slice",
                target_flow=flow or target_flow,
                target_step=step or target_step,
                target_lobes=narrowed,
                reason="context window is tight; trim optional recall/skill lobes for this step",
                confidence=0.8,
                queue=queue,
                observations=observations,
            )

    if obs := by_kind.get("empty_lobe_slice"):
        flow, step = _split_target(obs.target)
        if step in _PINNED_STEPS:
            return MetaDecision(
                action="meta_review",
                target_flow=flow or target_flow,
                target_step=step or target_step,
                target_lobes=current_lobes,
                reason=(
                    "pinned step has an empty lobe slice; cite/filter are never "
                    "skippable — needs review"
                ),
                confidence=0.4,
                queue=queue,
                observations=observations,
            )
        return MetaDecision(
            action="skip_step",
            target_flow=flow or target_flow,
            target_step=step or target_step,
            target_lobes=current_lobes,
            reason=obs.detail,
            confidence=0.75,
            queue=queue,
            observations=observations,
        )

    if obs := by_kind.get("empty_step_context"):
        flow, step = _split_target(obs.target)
        return MetaDecision(
            action="retry_step",
            target_flow=flow or target_flow,
            target_step=step or target_step,
            target_lobes=current_lobes,
            reason=obs.detail,
            confidence=0.65,
            queue=queue,
            observations=observations,
        )

    return MetaDecision(
        action="continue",
        target_flow=target_flow,
        target_step=target_step,
        target_lobes=current_lobes,
        reason="observations do not require regulation",
        confidence=0.9,
        queue=queue,
        observations=observations,
    )
