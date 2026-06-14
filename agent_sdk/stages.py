"""First-class ``Stage`` — one execution unit, ``Activable`` like a Lobe/Flow.

A **stage** is one execution unit: a slice of lobes it consults, a loop mode, and
its tools (api.md §5). Like a Lobe or a Skill it carries the uniform
``id`` / ``name`` / ``description`` / ``use_when`` / ``signal`` surface — its
``signal`` gates whether the step runs this turn (0 = skip).

Author it two ways::

    # class form — the full Activable surface (mirrors Lobe authoring)
    class Research(Stage):
        id, name = "research", "Research"
        description = "Gather evidence from sources."
        use_when = "the question needs external facts"
        lobes = ("research",)
        loop = "agentic"
        tools = ("search",)
        def signal(self, ctx) -> float:
            return 1.0 if ctx.get("needs_sources") else 0.0

    # concise builder — signal defaults to always-on
    stage("plan", lobes=["plan"])

``loop`` ∈ ``none`` (pure prompt) · ``single`` (one LLM call) · ``agentic`` (a
ReAct ``tool_loop``) · ``map`` (fan-out over a scratchpad key). Per-stage
overrides: ``model`` / ``temperature`` / ``max_tokens`` / ``hops`` /
``system_prompt``. A Stage compiles to the internal :class:`FlowStep` the runtime
consumes via :meth:`Stage.to_flow_step`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from agent_sdk.flows.flow import FlowStep

__all__ = ["Stage", "stage", "StageRegistry"]


class Stage:
    """One execution unit, ``Activable``. Subclass (class form) or use ``stage()``."""

    # ── Activable surface ────────────────────────────────────────────────────
    id: str = ""
    name: str = ""
    description: str = ""
    use_when: str = ""

    # ── execution shape ──────────────────────────────────────────────────────
    lobes: tuple[str, ...] = ()
    loop: str = "single"  # none | single | agentic | map
    tools: tuple[str, ...] = ()
    fanout_key: str = ""
    threshold: float = 0.0  # min activation to run; 0 = always-on (default)

    # ── fan-out shape (``loop="map"`` only; defaults reproduce today's behavior) ──
    # False ⇒ sequential state-carry (each worker sees prior results as notes — the
    # tasks-plugin rail relies on this). True ⇒ workers run concurrently (asyncio.gather,
    # bounded by ``fanout_max``), no state-carry — independent fan-out (doc 12).
    fanout_parallel: bool = False
    fanout_max: int = 40  # concurrency cap / item ceiling (always clamped ≤ 40)
    # False ⇒ workers share the turn's evidence pool (today). True ⇒ each worker gets a
    # FRESH retrieved_chunks/already_read — worker A's chunks never enter worker B's
    # window; only its result (memo) returns. True per-worker context isolation (doc 12).
    fanout_isolated: bool = False

    # ── per-stage inference overrides (None ⇒ engine/policy default) ─────────
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    hops: int | None = None
    system_prompt: str | None = None

    def __init__(
        self,
        id: str | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        use_when: str | None = None,
        lobes: Sequence[str] | None = None,
        loop: str | None = None,
        tools: Sequence[str] | None = None,
        fanout_key: str | None = None,
        fanout_parallel: bool | None = None,
        fanout_max: int | None = None,
        fanout_isolated: bool | None = None,
        threshold: float | None = None,
        signal: Callable[[dict], float] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        hops: int | None = None,
        system_prompt: str | None = None,
    ) -> None:
        # Instance overrides win over class attributes; unset args keep the
        # class-level value (so a Stage subclass with class attributes still
        # constructs with no args).
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
        if lobes is not None:
            self.lobes = tuple(lobes)
        if loop is not None:
            self.loop = loop
        if tools is not None:
            self.tools = tuple(tools)
        if fanout_key is not None:
            self.fanout_key = fanout_key
        if fanout_parallel is not None:
            self.fanout_parallel = fanout_parallel
        if fanout_max is not None:
            self.fanout_max = fanout_max
        if fanout_isolated is not None:
            self.fanout_isolated = fanout_isolated
        if threshold is not None:
            self.threshold = threshold
        if model is not None:
            self.model = model
        if temperature is not None:
            self.temperature = temperature
        if max_tokens is not None:
            self.max_tokens = max_tokens
        if hops is not None:
            self.hops = hops
        if system_prompt is not None:
            self.system_prompt = system_prompt
        self._signal_fn = signal

    def signal(self, ctx: dict) -> float:
        """Deterministic, free activation in [0, 1] — gates the step (0 = skip).

        Defaults to always-on (1.0). The ``stage()`` builder's ``signal=`` arg or
        a subclass override customizes it.
        """
        if self._signal_fn is not None:
            return float(self._signal_fn(ctx))
        return 1.0

    def to_flow_step(self) -> FlowStep:
        """Compile to the internal ``FlowStep`` the runtime consumes."""
        if not self.id:
            raise ValueError("Stage requires a non-empty id")
        sid = self.id
        return FlowStep(
            name=sid,
            lobes=tuple(self.lobes),
            loop=self.loop,
            tools=tuple(self.tools),
            description=self.description,
            fanout_key=self.fanout_key,
            fanout_parallel=self.fanout_parallel,
            fanout_max=self.fanout_max,
            fanout_isolated=self.fanout_isolated,
            signals=lambda ctx, _self=self: {_self.id: _self.signal(ctx)},
            signal_weights={sid: 1.0},
            min_activation=float(self.threshold),
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            hops=self.hops,
            system_prompt=self.system_prompt,
        )

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"Stage(id={self.id!r}, loop={self.loop!r}, lobes={self.lobes})"


def stage(
    id: str,
    *,
    name: str | None = None,
    description: str = "",
    use_when: str = "",
    lobes: Sequence[str] = (),
    loop: str = "single",
    tools: Sequence[str] = (),
    fanout_key: str = "",
    fanout_parallel: bool | None = None,
    fanout_max: int | None = None,
    fanout_isolated: bool | None = None,
    threshold: float = 0.0,
    signal: Callable[[dict], float] | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    hops: int | None = None,
    system_prompt: str | None = None,
) -> Stage:
    """Concise builder for a simple stage (signal defaults to always-on)."""
    return Stage(
        id,
        name=name,
        description=description,
        use_when=use_when,
        lobes=lobes,
        loop=loop,
        tools=tools,
        fanout_key=fanout_key,
        fanout_parallel=fanout_parallel,
        fanout_max=fanout_max,
        fanout_isolated=fanout_isolated,
        threshold=threshold,
        signal=signal,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        hops=hops,
        system_prompt=system_prompt,
    )


class StageRegistry:
    """Per-turn view of the stage table — id → Stage; resolves flow references.

    A flow lists stage **ids**; ``resolve`` expands an id list against this table
    so the same stage is freely combined into many flows, never bound to one.
    """

    def __init__(self, stages: Sequence[Stage] | None = None):
        self._stages: dict[str, Stage] = {}
        for s in stages or []:
            self.register(s)

    def register(self, s: Stage) -> None:
        if not s.id:
            raise ValueError("cannot register a Stage with an empty id")
        self._stages[s.id] = s

    def get(self, stage_id: str) -> Stage | None:
        return self._stages.get(stage_id)

    def stages(self) -> list[Stage]:
        return list(self._stages.values())

    def ids(self) -> list[str]:
        return list(self._stages.keys())

    def resolve(self, stage_ids: Sequence[str]) -> list[Stage]:
        """Expand a flow's stage-id references; unknown ids are skipped."""
        out: list[Stage] = []
        for sid in stage_ids:
            s = self._stages.get(sid)
            if s is not None:
                out.append(s)
        return out
