"""Data model for the metacognition layer.

Metacognition is the meta level over the object-level OX/OY engine:
lobes optimize context, flow stages optimize progressive execution, and the
meta layer monitors both before deciding what to think about next.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

MetaAction = Literal[
    "continue",
    "adjust_lobe_slice",
    "retry_step",
    "skip_step",
    "ask_clarification",
    "meta_review",
    # Navigator layer — pipeline-level (phase) moves, applied by the engine's
    # movable phase cursor (apply-capable, opt-in; never default → parity).
    "redo_phase",
    "goto_phase",
    # Layer-1 dynamic state machine — compile a plan into a sequence of states and
    # expand the rail (e.g. ``act → act → act`` over a plan's subjects). Apply-gated,
    # default off; the compiled plan lives on ``scratchpad["state_plan"]``.
    "expand",
]

# The canonical reasoning states a state plan may schedule (docs/concepts/15). ``act`` is the
# repeatable workhorse; ``cite``/``filter`` are the pinned grounding states and are appended by
# the compiler when the turn grounds — never omitted.
STATE_PLAN_KEY = "state_plan"


def compile_state_plan(
    aspects: list[dict] | list[str], *, grounds: bool = False
) -> list[dict]:
    """Compile a plan into a dynamic sequence of structural states (Layer 1).

    Pure + deterministic: each aspect becomes an ``act`` state scoped to that aspect's subject
    (the ``act → act → act`` pattern); a single ``synthesize`` folds the results; when the turn
    ``grounds`` the pinned ``cite`` then ``filter`` states are appended (never dropped). One aspect
    (or none) degrades to a single ``act`` — no fan-out, no loss. Returns
    ``[{"state": str, "subject": str|None}, …]`` for ``scratchpad["state_plan"]``.
    """
    subjects: list[str] = []
    for a in aspects or []:
        if isinstance(a, str):
            subjects.append(a.strip())
        elif isinstance(a, dict):
            subjects.append(str(a.get("question") or a.get("subject") or a.get("id") or "").strip())
    subjects = [s for s in subjects if s]

    plan: list[dict] = []
    if len(subjects) <= 1:
        plan.append({"state": "act", "subject": subjects[0] if subjects else None})
    else:
        plan.extend({"state": "act", "subject": s} for s in subjects)
        plan.append({"state": "synthesize", "subject": None})
    if grounds:
        plan.append({"state": "cite", "subject": None})
        plan.append({"state": "filter", "subject": None})
    return plan


@dataclass(frozen=True)
class MetaObservation:
    id: str
    kind: str
    target: str
    severity: float = 0.0
    detail: str = ""

    def to_payload(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MetaQueueItem:
    target: str
    reason: str
    priority: float = 0.0

    def to_payload(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MetaDecision:
    action: MetaAction = "continue"
    target_flow: str | None = None
    target_step: str | None = None
    target_lobes: tuple[str, ...] = ()
    weight_patch: dict[str, float] = field(default_factory=dict)
    reason: str = "metacognition disabled or no regulation needed"
    confidence: float = 1.0
    queue: tuple[MetaQueueItem, ...] = ()
    observations: tuple[MetaObservation, ...] = ()

    def to_payload(self) -> dict:
        payload = asdict(self)
        payload["queue"] = [item.to_payload() for item in self.queue]
        payload["observations"] = [obs.to_payload() for obs in self.observations]
        return payload


@dataclass(frozen=True)
class MetaState:
    enabled: bool
    observations: tuple[MetaObservation, ...] = ()
    decision: MetaDecision = field(default_factory=MetaDecision)

    def to_payload(self) -> dict:
        return {
            "enabled": self.enabled,
            "observations": [obs.to_payload() for obs in self.observations],
            "decision": self.decision.to_payload(),
        }
