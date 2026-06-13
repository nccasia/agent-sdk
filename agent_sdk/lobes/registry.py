"""LobeRegistry — the per-turn view of the lobe network (mirrors SkillRegistry).

Defaults are the degenerate network; rows (dicts) override or extend by
id/name. ``from_rows``/``add_row`` are the G6 seam: a new capability is a
registry row with signals + edges + a receptive field + write-back kinds —
never an interpreter branch. Every mutation re-validates the forward DAG and
the pinned-edge protection (``validate_network``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_sdk.lobes.rows import compile_row_recognizer, compile_row_signals
from agent_sdk.lobes.runtime import BaseLobe, TurnContext
from agent_sdk.network.activation import (
    LAYER_COGNITION,
    ContextBound,
    LobeSpec,
    PathSpec,
    validate_network,
)
from agent_sdk.network.context_builder import ContextNode

# The deprecated stage axis is opaque to the SDK registry — it reads only a
# stage's ``.path`` / ``.name`` and never constructs one. ``Stage`` is aliased
# to ``Any`` so the annotations resolve without importing the project-side
# ``flows.compat`` glue.
Stage = Any

# Default-member providers — the PROJECT (``agent_core.lobes.network``)
# registers these at import time so the SDK registry carries no concrete
# lobe / path / stage instances (framework, not instances). They default to
# empty for standalone SDK use.
def _empty_list() -> list:
    return []


_default_lobe_objects: Callable[[], list[BaseLobe]] = _empty_list
_default_paths: Callable[[], list[PathSpec]] = _empty_list
_default_stages: Callable[[], list[Any]] = _empty_list


def set_default_providers(
    *,
    lobe_objects: Callable[[], list[BaseLobe]] | None = None,
    paths: Callable[[], list[PathSpec]] | None = None,
    stages: Callable[[], list[Any]] | None = None,
) -> None:
    """Register the project's default lobe/path/stage providers (import time)."""
    global _default_lobe_objects, _default_paths, _default_stages
    if lobe_objects is not None:
        _default_lobe_objects = lobe_objects
    if paths is not None:
        _default_paths = paths
    if stages is not None:
        _default_stages = stages


class LobeRegistry:
    """Per-turn view of the lobe network — mirrors ``SkillRegistry``.

    Defaults are the degenerate network; rows (dicts) override or extend by
    id/name. ``from_rows``/``add_row`` are the G6 seam: a new capability is
    a registry row with signals + edges + a receptive field + write-back
    kinds — never an interpreter branch.

    Phase 7+ — the registry also holds the **stage axis** (the
    progressive-execution second axis): ``default_stages()`` provides the
    7 named paths' stage sequences; ``get_stage(name)`` and
    ``compose_stage_prompt(stage, ctx, weights)`` are the lookup + composer
    seams.
    """

    def __init__(
        self,
        lobes: list[LobeSpec] | None = None,
        paths: list[PathSpec] | None = None,
        lobe_objects: list[BaseLobe] | None = None,
        stages: list[Stage] | None = None,
    ):
        objects = lobe_objects if lobe_objects is not None else _default_lobe_objects()
        self._lobe_objects: dict[str, BaseLobe] = {lobe.id: lobe for lobe in objects}
        base = lobes if lobes is not None else [lobe.spec for lobe in objects]
        self._lobes: dict[str, LobeSpec] = {lobe.id: lobe for lobe in base}
        self._paths: dict[str, PathSpec] = {
            p.name: p for p in (paths if paths is not None else _default_paths())
        }
        # Phase 7+ — the stage axis. Stages are keyed by ``(path, name)``
        # so a single path can have multiple stages (research has 5;
        # task_schedule has 2; the rest have 1).
        stage_list = stages if stages is not None else _default_stages()
        self._stages: dict[tuple[str, str], Stage] = {(s.path, s.name): s for s in stage_list}
        validate_network(self.lobes())

    @classmethod
    def from_rows(
        cls, lobe_rows: list[dict] | None = None, path_rows: list[dict] | None = None
    ) -> LobeRegistry:
        registry = cls()
        for row in lobe_rows or []:
            registry.add_row(row)
        for row in path_rows or []:
            registry.add_path_row(row)
        return registry

    def lobes(self) -> list[LobeSpec]:
        return sorted(self._lobes.values(), key=lambda lobe: (lobe.layer, lobe.order, lobe.id))

    def paths(self) -> list[PathSpec]:
        return list(self._paths.values())

    def stages(self) -> list[Stage]:
        return list(self._stages.values())

    def stages_for_path(self, path_name: str) -> list[Stage]:
        """The default stage sequence for a path (Phase 7+).

        Returns the stages registered under ``(path_name, *)`` in their
        declaration order. Emergent paths have no stages by default — the
        caller is expected to handle that case (``[]`` returns here).
        """
        return [s for (p, _), s in self._stages.items() if p == path_name]

    def get(self, lobe_id: str) -> LobeSpec | None:
        return self._lobes.get(lobe_id)

    def get_lobe(self, lobe_id: str) -> BaseLobe | None:
        return self._lobe_objects.get(lobe_id)

    def get_path(self, name: str) -> PathSpec | None:
        return self._paths.get(name)

    def get_stage(self, path: str, name: str) -> Stage | None:
        """Phase 7+ — lookup a stage by ``(path, name)`` pair."""
        return self._stages.get((path, name))

    def register(self, lobe: LobeSpec) -> None:
        self._lobes[lobe.id] = lobe
        validate_network(self.lobes())

    def register_lobe(self, lobe: BaseLobe) -> None:
        self._lobe_objects[lobe.id] = lobe
        self.register(lobe.spec)

    def register_path(self, path: PathSpec) -> None:
        self._paths[path.name] = path

    def register_stage(self, stage: Stage) -> None:
        """Phase 7+ — register a stage (declarative G6 seam)."""
        self._stages[(stage.path, stage.name)] = stage

    def remove(self, lobe_id: str) -> None:
        self._lobes.pop(lobe_id, None)
        self._lobe_objects.pop(lobe_id, None)
        validate_network(self.lobes())

    def remove_path(self, name: str) -> None:
        self._paths.pop(name, None)

    def remove_stage(self, path: str, name: str) -> None:
        """Phase 7+ — remove a stage from the registry."""
        self._stages.pop((path, name), None)

    def compose_stage_prompt(
        self, stage: Stage, ctx: TurnContext, *, weights: dict | None = None
    ) -> list[ContextNode]:
        """Phase 7+ — the per-stage system prompt composer.

        The stage references a **slice of lobes** (the lobes it consults
        for context). For each lobe in the slice, the composer runs
        the lobe's LobeNode state machine (Phase 4) under the live
        TurnContext and collects the enabled nodes' ``ContextNode``s.

        The composer is the **bridge between the two axes**:
        - Lobe axis (Phase 4): each lobe emits a flat list of context
          chunks (ContextNodes) shaped by the live TurnContext.
        - Stage axis (Phase 7): this composer collects the lobe axis's
          output for the stage's slice, producing the stage's
          system prompt.

        A stage that consults no lobes (empty slice) returns an empty
        list — the stage's prompt is whatever the LLM call itself
        brings (e.g., a fixed task_execute format).
        """
        from agent_sdk.lobes.runtime import TurnContext as _TC

        if not stage.lobes:
            return []
        if not isinstance(ctx, _TC):
            # Defensive: callers should pass a TurnContext; if not, the
            # lobes' state machines can't fire (their signals depend on
            # it). Return empty rather than raising.
            return []
        # ``weights`` is reserved for Phase 7c+ per-bot lobe-slice
        # customization (stage_disable_<stage>__lobe_<id>__add, etc.).
        # The composer itself doesn't read it today — the lobes' state
        # machines do via the TurnContext.
        _ = weights  # noqa: F841 — explicit reservation
        out: list[ContextNode] = []
        for lobe_id in stage.lobes:
            lobe = self.get_lobe(lobe_id)
            if lobe is None:
                # Unknown lobe id in the stage's slice — skip rather
                # than raise. The structural test ``test_default_stages_lobe_slice_in_default_lobe_set``
                # catches this at the registry level.
                continue
            try:
                nodes = list(lobe.build_context(ctx) or [])
            except Exception:
                nodes = []
            out.extend(nodes)
        return out

    def add_row(self, row: dict) -> LobeSpec:
        """Register a lobe from a declarative registry row (no code)."""
        attends = row.get("attends") or {}
        lobe = LobeSpec(
            id=str(row["id"]),
            behavior=str(row.get("behavior") or "custom"),
            layer=int(row.get("layer", LAYER_COGNITION)),
            order=int(row.get("order", 99)),
            prior=float(row.get("prior", 0.0)),
            pinned=bool(row.get("pinned", False)),
            signals=compile_row_signals(row.get("signals")),
            signal_weights={str(k): float(v) for k, v in (row.get("signal_weights") or {}).items()},
            edges={str(k): float(v) for k, v in (row.get("edges") or {}).items()},
            writes=tuple(str(w) for w in (row.get("writes") or ())),
            min_activation=float(row.get("min_activation", 0.5)),
            attends=ContextBound(
                kinds=tuple(str(k) for k in (attends.get("kinds") or ())),
                scopes=tuple(str(s) for s in (attends.get("scopes") or ())),
                budget_tokens=int(attends.get("budget_tokens", 1600)),
                min_activation=float(attends.get("min_activation", 0.22)),
            ),
        )
        self.register(lobe)
        return lobe

    def add_path_row(self, row: dict) -> PathSpec:
        """Register a named path from a declarative registry row — promotion
        of a recurring emergent shape is exactly this call plus defaults."""
        path = PathSpec(
            name=str(row["name"]),
            members=tuple(str(m) for m in (row.get("members") or ())),
            recognizer=compile_row_recognizer(row.get("recognizer")),
            bias={str(k): float(v) for k, v in (row.get("bias") or {}).items()},
            threshold=float(row.get("threshold", 0.5)),
        )
        self.register_path(path)
        return path
