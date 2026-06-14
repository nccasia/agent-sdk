"""ctxvar_resolve — B2 recall lobe for admin-selected context variables.

Behavior: loads the system-resolved context block
(`_load_resolved_context_block`, one indexed SELECT per turn) and writes a
`ctxvar` node. Always-on at parity (prior 1.0).

Tuning keys: `prior_ctxvar_resolve` (1.0), `min_ctxvar_resolve` (0.5),
`budget_ctxvar_resolve` (1600).
Gates: degenerate-parity matrix; attentionbench `bounded`.

Phase 4+ — the lobe is a state machine of one opt-in node:

- ``ctxvar:resolved`` — fires when there are any resolved context
  variables. Produces 1 ``ctxvar`` ContextNode per variable. Per-bot
  disable: ``disable_ctxvar_resolve_ctxvar:resolved``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from agent_sdk.lobes.runtime import BaseLobe, PromptContribution, TurnContext
from agent_sdk.network.activation import LAYER_MEMORY, ContextBound, LobeNode, LobeSpec
from agent_sdk.network.context_builder import ContextNode

HEADER = "## Context\nServer-resolved facts for this turn (authoritative, current):\n"


async def load(
    session_factory: Callable[[], AsyncIterator[Any]],
    redis: Any,
    *,
    tenant_id: str,
    bot_id: str,
    channel_id: str,
    clan_id: str | None,
    user_id: str,
    lang: str,
    timezone: str,
    bot_name: str,
    resolved: list[tuple[str, str]] | None = None,
) -> tuple[str, list[tuple[str, str]]]:
    """Render enabled context variables (admin-resolved, recomputed per turn).

    Leaf-safe: the SDK has no backend ``context_variables`` table, so resolution
    is supplied by the host — pass ``resolved=[(key, value), ...]`` (e.g. from a
    prefetch hook). Static values render verbatim; an empty list ⇒ no ctxvars.
    The ``session_factory`` / ``redis`` params are accepted for signature parity
    and ignored.
    """
    _ = (
        session_factory,
        redis,
        tenant_id,
        bot_id,
        channel_id,
        clan_id,
        user_id,
        lang,
        timezone,
        bot_name,
    )
    lines = [f"- {key}: {value}" for key, value in (resolved or []) if value]
    fragments = [(line.split(":", 1)[0].lstrip("- ").strip(), line) for line in lines]
    if not lines:
        return "", fragments
    return HEADER + "\n".join(lines), fragments


def nodes(fragments: list[tuple[str, str]]) -> list[ContextNode]:
    return [
        ContextNode(
            id=f"ctxvar:{key}",
            kind="ctxvar",
            text=line,
            stability="slow",
            embed_text=line,
            menu_hint=f"resolved variable: {key}",
        )
        for key, line in fragments
    ]


def signals(_ctx: dict) -> dict[str, float]:
    return {}  # prior-driven: parity with today's unconditional loads


# Phase 4+ — per-lobe signal vocabulary for the ctxvar state machine.
def _ctxvar_signal_ctx(ctx: TurnContext) -> dict:
    """Build the ctxvar-signal dict from the TurnContext.

    One flag: has_resolved — there are resolved context variables.
    """
    has_resolved = (
        bool(str(ctx.lobe_outputs.get("ctxvar_resolve") or "").strip())
        if ctx.lobe_outputs
        else False
    )
    return {"has_resolved": 1.0 if has_resolved else 0.0}


def _signals_for(ctx_dict: dict, node_id: str) -> dict[str, float]:
    return {"has_resolved": 1.0 if ctx_dict.get("has_resolved") else 0.0}


def _node_ctxvar_resolved(lobe_id: str) -> LobeNode:
    """The resolved context variables — fires when any are resolved."""

    def _produce(ctx: TurnContext) -> list[ContextNode]:
        block = str(ctx.lobe_outputs.get("ctxvar_resolve") or "")
        if not block:
            return []
        # Parse the block into per-variable rows (one ContextNode each).
        lines = [ln for ln in block.split("\n") if ln.strip().startswith("- ")]
        out: list[ContextNode] = []
        for line in lines:
            key = line.split(":", 1)[0].lstrip("- ").strip()
            out.append(
                ContextNode(
                    id=f"ctxvar:{key}",
                    kind="ctxvar",
                    text=line,
                    stability="slow",
                    embed_text=line,
                    menu_hint=f"resolved variable: {key}",
                )
            )
        return out

    def _signals(_ctx: dict) -> dict[str, float]:
        return _signals_for(_ctx, "ctxvar:resolved")

    return LobeNode(
        id="ctxvar:resolved",
        lobe_id=lobe_id,
        layer=LAYER_MEMORY,
        stability="slow",
        prior=0.0,
        signals=_signals,
        signal_weights={"has_resolved": 1.0},
        min_activation=0.5,
        order=0,
        description="the resolved context variables (1 ContextNode per variable)",
        produce=_produce,
        prompt=lambda _ctx: [],
    )


SPEC = LobeSpec(
    id="ctxvar_resolve",
    behavior="recall",
    layer=LAYER_MEMORY,
    order=3,
    prior=1.0,
    signals=signals,
    attends=ContextBound(kinds=("ctxvar",)),
    writes=("ctxvar",),
)


class CtxvarResolveLobe(BaseLobe):
    """Executable admin context-variable recall lobe (Phase 4+ state machine)."""

    spec = SPEC
    HEADER = HEADER

    def signals(self, ctx: dict) -> dict[str, float]:
        return signals(ctx)

    def prompt(self, ctx: TurnContext) -> list[PromptContribution]:
        block = str(ctx.lobe_outputs.get(self.id) or "")
        if not block:
            return []
        return [PromptContribution(block, stability="slow", source=self.id)]

    async def load(
        self,
        session_factory: Callable[[], AsyncIterator[Any]],
        redis: Any,
        *,
        tenant_id: str,
        bot_id: str,
        channel_id: str,
        clan_id: str | None,
        user_id: str,
        lang: str,
        timezone: str,
        bot_name: str,
        _ctx: TurnContext | None = None,
    ) -> tuple[str, list[tuple[str, str]]]:
        return await load(
            session_factory,
            redis,
            tenant_id=tenant_id,
            bot_id=bot_id,
            channel_id=channel_id,
            clan_id=clan_id,
            user_id=user_id,
            lang=lang,
            timezone=timezone,
            bot_name=bot_name,
        )

    def nodes(
        self, fragments: list[tuple[str, str]], *, _ctx: TurnContext | None = None
    ) -> list[ContextNode]:
        return nodes(fragments)

    def _signal_ctx_for(self, ctx: TurnContext) -> dict:
        """Override the default — the ctxvar lobe injects has_resolved on top
        of the cross-section defaults."""
        base = super()._signal_ctx_for(ctx)
        base.update(_ctxvar_signal_ctx(ctx))
        return base

    def state_machine(self) -> list[LobeNode]:
        """One opt-in node — the ctxvar lobe's state machine."""
        return [_node_ctxvar_resolved(self.id)]


LOBE = CtxvarResolveLobe()
