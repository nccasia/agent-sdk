"""session_recall — B2 recall lobe for session memory (summary + facts).

Behavior: writes `session_summary` / `session_fact` nodes from the loaded
session memory. Always-on at parity (prior 1.0 ≥ threshold 0.5) — the lobe
form of today's unconditional one-pass load; quieting it per-bot
(`prior_session_recall: 0`) silences the whole segment.

Tuning keys: `prior_session_recall` (1.0), `min_session_recall` (0.5),
`budget_session_recall` (1600).
Gates: degenerate-parity matrix; attentionbench `bounded`.

Phase 4+ — the lobe is a state machine of two opt-in nodes:

- ``session:summary`` — fires when the session has a non-empty summary.
  Produces a ``session_summary`` ContextNode. Per-bot disable:
  ``disable_session_recall_session:summary``.
- ``session:facts`` — fires when the session has any known facts.
  Produces 1 ContextNode per fact. Per-bot disable:
  ``disable_session_recall_session:facts``.
"""

from __future__ import annotations

from agent_sdk.lobes.runtime import BaseLobe, PromptContribution, TurnContext
from agent_sdk.network.activation import LAYER_MEMORY, ContextBound, LobeNode, LobeSpec
from agent_sdk.network.context_builder import ContextNode


def render_block(session_memory) -> str:
    """Render rolling summary + known facts for the system prompt."""
    if not session_memory:
        return ""
    sections: list[str] = []
    if session_memory.summary:
        sections.append("## Prior conversation summary\n" + session_memory.summary.strip())
    if session_memory.facts:
        fact_lines = [f"- {k}: {v}" for k, v in session_memory.facts.items()]
        sections.append("## Known facts about this conversation\n" + "\n".join(fact_lines))
    return "\n\n".join(sections)


def nodes(session_memory) -> list[ContextNode]:
    """Session summary/fact write-back nodes."""
    out: list[ContextNode] = []
    if not session_memory:
        return out
    if session_memory.summary:
        out.append(
            ContextNode(
                id="session:summary",
                kind="session_summary",
                text=str(session_memory.summary)[:400],
            )
        )
    for key, value in (session_memory.facts or {}).items():
        out.append(
            ContextNode(
                id=f"session:fact/{key}",
                kind="session_fact",
                text=f"{key}: {value}"[:200],
            )
        )
    return out


def signals(_ctx: dict) -> dict[str, float]:
    return {}  # prior-driven: parity with today's unconditional loads


# Phase 4+ — per-lobe signal vocabulary for the session state machine.
def _session_signal_ctx(ctx: TurnContext) -> dict:
    """Build the session-signal dict from the TurnContext.

    Two flags the session lobe reads:

    - has_summary: the session has a non-empty summary
    - has_facts: the session has at least one known fact
    """
    sm = ctx.session_memory
    has_summary = bool(getattr(sm, "summary", None)) if sm else False
    has_facts = bool(getattr(sm, "facts", None)) if sm else False
    return {
        "has_summary": 1.0 if has_summary else 0.0,
        "has_facts": 1.0 if has_facts else 0.0,
    }


def _signals_for(ctx_dict: dict, node_id: str) -> dict[str, float]:
    if node_id == "session:summary":
        return {"has_summary": 1.0 if ctx_dict.get("has_summary") else 0.0}
    if node_id == "session:facts":
        return {"has_facts": 1.0 if ctx_dict.get("has_facts") else 0.0}
    return {}


def _node_session_summary(lobe_id: str) -> LobeNode:
    """The session summary — fires when the session has a non-empty summary."""

    def _produce(ctx: TurnContext) -> list[ContextNode]:
        sm = ctx.session_memory
        if not sm or not getattr(sm, "summary", None):
            return []
        return [
            ContextNode(
                id="session:summary",
                kind="session_summary",
                text=str(sm.summary)[:400],
            )
        ]

    def _signals(_ctx: dict) -> dict[str, float]:
        return _signals_for(_ctx, "session:summary")

    return LobeNode(
        id="session:summary",
        lobe_id=lobe_id,
        layer=LAYER_MEMORY,
        stability="volatile",
        prior=0.0,
        signals=_signals,
        signal_weights={"has_summary": 1.0},
        min_activation=0.5,
        order=0,
        description="the session summary (volatile, per-turn)",
        produce=_produce,
        prompt=lambda _ctx: [],
    )


def _node_session_facts(lobe_id: str) -> LobeNode:
    """The session facts — fires when the session has any known facts."""

    def _produce(ctx: TurnContext) -> list[ContextNode]:
        sm = ctx.session_memory
        if not sm or not getattr(sm, "facts", None):
            return []
        return [
            ContextNode(
                id=f"session:fact/{k}",
                kind="session_fact",
                text=f"{k}: {v}"[:200],
            )
            for k, v in sm.facts.items()
        ]

    def _signals(_ctx: dict) -> dict[str, float]:
        return _signals_for(_ctx, "session:facts")

    return LobeNode(
        id="session:facts",
        lobe_id=lobe_id,
        layer=LAYER_MEMORY,
        stability="volatile",
        prior=0.0,
        signals=_signals,
        signal_weights={"has_facts": 1.0},
        min_activation=0.5,
        order=1,
        description="the session facts (1 ContextNode per known fact)",
        produce=_produce,
        prompt=lambda _ctx: [],
    )


SPEC = LobeSpec(
    id="session_recall",
    behavior="recall",
    layer=LAYER_MEMORY,
    order=1,
    prior=1.0,
    signals=signals,
    attends=ContextBound(kinds=("session_summary", "session_fact")),
    writes=("session_fact", "session_summary"),
)


class SessionRecallLobe(BaseLobe):
    """Executable session-memory recall lobe (Phase 4+ state machine)."""

    spec = SPEC

    def signals(self, ctx: dict) -> dict[str, float]:
        return signals(ctx)

    def prompt(self, ctx: TurnContext) -> list[PromptContribution]:
        block = render_block(ctx.session_memory)
        if not block:
            return []
        return [PromptContribution(block, stability="volatile", source=self.id)]

    def render_block(self, session_memory, *, _ctx: TurnContext | None = None) -> str:
        return render_block(session_memory)

    def nodes(self, session_memory, *, _ctx: TurnContext | None = None) -> list[ContextNode]:
        return nodes(session_memory)

    def _signal_ctx_for(self, ctx: TurnContext) -> dict:
        """Override the default — the session lobe injects has_summary /
        has_facts signal vocabulary on top of the cross-section defaults."""
        base = super()._signal_ctx_for(ctx)
        base.update(_session_signal_ctx(ctx))
        return base

    def state_machine(self) -> list[LobeNode]:
        """Two opt-in nodes — the session lobe's state machine."""
        return [
            _node_session_summary(self.id),
            _node_session_facts(self.id),
        ]


LOBE = SessionRecallLobe()
