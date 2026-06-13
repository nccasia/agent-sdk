"""Shared OX stage helpers for flow-axis definitions."""

from __future__ import annotations

from collections.abc import Callable

from agent_sdk.flows.flow import FlowStep, FlowStepNode


def _context_tokens(ctx: dict) -> float:
    for key in ("context_tokens", "tokens_in", "estimated_context_tokens"):
        value = ctx.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _ctx_tight_signals(ctx: dict) -> dict[str, float]:
    tokens = _context_tokens(ctx)
    return {"context_window_tight": 1.0 if tokens >= 6000 else 0.0}


def _ctx_open_signals(ctx: dict) -> dict[str, float]:
    tokens = _context_tokens(ctx)
    return {"context_window_open": 1.0 if 0 < tokens <= 2500 else 0.0}


def context_window_nodes(flow: str, step: str) -> tuple[FlowStepNode, ...]:
    """Default reactive state nodes for the OX stage axis.

    They do not mutate execution directly. They provide an inspectable,
    per-stage signal surface that optimizers can use to tune lobe slices,
    step budgets, or step enablement based on the live OY context window.
    """
    return (
        FlowStepNode(
            id="context:tight",
            flow=flow,
            step=step,
            signals=_ctx_tight_signals,
            signal_weights={"context_window_tight": 1.0},
            min_activation=0.5,
            description="context window is near pressure; optimizer may trim this step",
        ),
        FlowStepNode(
            id="context:open",
            flow=flow,
            step=step,
            signals=_ctx_open_signals,
            signal_weights={"context_window_open": 1.0},
            min_activation=0.5,
            description="context window has room; optimizer may widen this step",
        ),
    )


def stage(
    *,
    flow: str,
    name: str,
    lobes: tuple[str, ...],
    # RFC 0017: stages are mostly ReAct — the canonical builder defaults to an
    # agentic loop. Existing default flows pass ``loop`` explicitly, so this is
    # parity-preserving; only NEW stages built without an explicit loop change.
    loop: str = "agentic",
    tools: tuple[str, ...] = (),
    description: str = "",
    fanout_key: str = "",
) -> FlowStep:
    """Build one flow-axis stage with the standard context-window state surface."""
    return FlowStep(
        name=name,
        lobes=lobes,
        loop=loop,
        tools=tools,
        description=description,
        fanout_key=fanout_key,
        state_nodes=context_window_nodes(flow, name),
    )


class Stage:
    """Skill-style authoring API for a flow stage — ONE self-describing class that
    answers *what / when / how* on its own, like the ``Lobe`` authoring class
    (and ``lobes/tools/tool_select.py``). It bundles the stage's identity, its
    running model, its lobe slice + tools, its inference config, and (the deeper
    step) its ReAct ``run`` behavior. ``.spec`` compiles to the internal
    ``FlowStep`` the flow registry + runner consume — byte-identical at parity,
    exactly as ``Lobe.spec`` compiles to ``LobeSpec``.

    A named stage (plan / research / synthesize / cite / filter / format / …) is
    the known reasoning STATE; ``type`` (the friendly name of ``loop``) is its
    running model — react | simple | map | none. Stages are mostly ReAct.
    """

    # ── self-description (a stage's file should answer what/when/how on its own)
    id: str = ""              # the stage NAME (its known reasoning state)
    flow: str = ""            # which flow this stage belongs to
    name: str = ""            # display name (defaults to id)
    description: str = ""     # WHAT it does (one line)
    use_when: str = ""        # WHEN — the path/intent it serves (doc + future activation)
    how: str = ""             # HOW it runs (mechanism, prose)
    # ── running model + slice + tools
    loop: str = "agentic"     # running model: agentic(react) | single(simple) | map | none
    lobes: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    fanout_key: str = ""
    # ── inference config (RFC 0017 — None ⇒ engine/policy default)
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    system_prompt: str | None = None
    # ── optional activation signal (default: structural always-on, no signal node)
    signal_weights: dict[str, float] = {}

    def activation(self, ctx: dict) -> float:
        """The stage's free, deterministic activation signal. Default 1.0 —
        stages are structural (always run when their flow is selected). A
        conditional stage overrides this AND sets ``signal_weights`` to gate
        inclusion; otherwise no signal node is emitted (byte-identical)."""
        return 1.0

    @property
    def spec(self) -> FlowStep:
        sid = self.id or self.name
        signals = None
        sweights: dict[str, float] = dict(self.signal_weights)
        if sweights:
            signals = lambda ctx: {sid: float(self.activation(ctx))}  # noqa: E731
        return FlowStep(
            name=sid,
            lobes=tuple(self.lobes),
            loop=self.loop,
            tools=tuple(self.tools),
            fanout_key=self.fanout_key,
            description=self.description,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            system_prompt=self.system_prompt,
            state_nodes=context_window_nodes(self.flow, sid),
            **({"signals": signals, "signal_weights": sweights, "min_activation": 0.5}
               if signals is not None else {}),
        )

    # The ReAct behavior lives here in the target design (the loop currently in
    # interpreter._run_flow_step). Declared now so a stage file fully describes
    # its execution; wired in the run()-extraction step.
    run: Callable | None = None
