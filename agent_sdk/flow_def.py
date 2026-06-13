"""Façade ``Flow`` — an intent pipeline: a list of Stage **ids** + its own signal.

A flow is an ordered list of Stage ids — the same stage is freely combined into
many flows, never bound to one. A Flow is ``Activable``: its ``signal`` /
``use_when`` recognize the turn's intent (this replaces the separate "path
recognizer"; the highest-scoring flow over threshold wins, else *emergent*).

    Flow("research", use_when="multi-step questions needing sources",
         stages=["plan", "research", "synthesize"])

This is the engine-facing flow definition. It compiles to the activation core's
``PathSpec`` (for lobe biasing) via :meth:`to_path_spec`, and its ``stages`` are
resolved against the ``StageRegistry`` at run time.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from agent_sdk.network.activation import PathSpec
from agent_sdk.signals import compile_signal

__all__ = ["Flow", "flow"]


class Flow:
    """An intent pipeline, ``Activable``. Subclass or use the ``flow()`` builder."""

    id: str = ""
    name: str = ""
    description: str = ""
    use_when: str = ""
    stages: tuple[str, ...] = ()
    threshold: float = 0.5
    grounds: bool = True  # produces a grounded (citable) reply

    def __init__(
        self,
        id: str | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        use_when: str | None = None,
        stages: Sequence[str] | None = None,
        threshold: float | None = None,
        grounds: bool | None = None,
        signal: Callable[[dict], float] | dict | float | None = None,
    ) -> None:
        if id is not None:
            self.id = id
        if name is not None:
            self.name = name
        if not self.name:
            self.name = self.id
        if description is not None:
            self.description = description
        if use_when is not None:
            self.use_when = use_when
        if stages is not None:
            self.stages = tuple(stages)
        if threshold is not None:
            self.threshold = threshold
        if grounds is not None:
            self.grounds = grounds
        # ``signal`` may be a callable, a declarative expression, or None.
        # Retain the declarative form (when given) so ``spec()`` round-trips
        # recognition faithfully.
        self.signal_expr: Any = None
        if signal is None:
            self._signal_fn: Callable[[dict], float] | None = None
        elif callable(signal):
            self._signal_fn = signal
        else:
            self.signal_expr = signal
            self._signal_fn = compile_signal(signal)

    def signal(self, ctx: dict) -> float:
        """Recognition score in [0, 1] (deterministic, free). Default 0.0."""
        if self._signal_fn is not None:
            return float(self._signal_fn(ctx))
        return 0.0

    def to_path_spec(
        self, members: Sequence[str], *, bias: dict[str, float] | None = None
    ) -> PathSpec:
        """Compile to the activation core's ``PathSpec`` (recognizer + lobe bias)."""
        return PathSpec(
            name=self.id,
            members=tuple(members),
            recognizer=lambda ctx: self.signal(ctx),
            bias=dict(bias or {}),
            threshold=self.threshold,
            stage_names=tuple(self.stages),
            grounds=self.grounds,
        )

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"Flow(id={self.id!r}, stages={self.stages})"


def flow(
    id: str,
    *,
    name: str | None = None,
    description: str = "",
    use_when: str = "",
    stages: Sequence[str] = (),
    threshold: float = 0.5,
    grounds: bool = True,
    signal: Callable[[dict], float] | dict | float | None = None,
) -> Flow:
    """Concise builder for a flow."""
    return Flow(
        id,
        name=name,
        description=description,
        use_when=use_when,
        stages=stages,
        threshold=threshold,
        grounds=grounds,
        signal=signal,
    )
