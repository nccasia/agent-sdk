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
]


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
