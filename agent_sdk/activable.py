"""The uniform ``Activable`` interface + the ``Layer`` enum.

Every PreAct building block — Lobe, Stage, Flow, Skill — is ``Activable``: it
shares one five-field interface so the framework reads uniformly, activates by
the same free + deterministic rule, and serializes identically (api.md §5).

    id           stable identifier
    name         display name
    description  WHAT it is (one line)
    use_when     WHEN — natural-language trigger (doc + optional semantic activation)
    signal(ctx)  the deterministic, free activation in [0, 1] (0 = dark)

``signal`` is never an LLM call. ``use_when`` doubles as documentation and the
source for an optional semantic-activation path (embed ``use_when`` vs the
query) — kept a separate, declared term so the free core stays reproducible.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Protocol, runtime_checkable

__all__ = ["Layer", "Activable"]


class Layer(IntEnum):
    """The reasoning layers, brain-shaped (RFC 0015).

    B0/B1 (instinct, perception) are core machinery and hold no lobes; lobes
    live in B2..B5. The integer values match ``network.activation.LAYER_*`` so a
    ``Layer`` is interchangeable with the raw int the activation core consumes.
    """

    INSTINCT = 0
    PERCEPTION = 1
    MEMORY = 2
    SKILL = 3
    COGNITION = 4
    EXPRESSION = 5


@runtime_checkable
class Activable(Protocol):
    """The shared interface for Lobes, Stages, Flows, and Skills."""

    id: str
    name: str
    description: str
    use_when: str

    def signal(self, ctx: dict) -> float:
        """Deterministic, free activation in [0, 1]. 0 = dark."""
        ...
