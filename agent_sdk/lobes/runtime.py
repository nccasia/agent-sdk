"""Shared substrate for lobe runtime behaviors (RFC 0015 — behavior extraction).

Every lobe behavior (`run(...)` in a lobe module) executes against the
``LlmCall`` seam — one narrow, injectable protocol — so a behavior is unit-
testable with a ``FakeLlm`` (tests/lobes/conftest.py) and optimizable without
constructing the interpreter. The interpreter provides the production
implementation (``BotPolicyInterpreter._lobe_llm``) wrapping per-stage model
resolution + ``client.messages.create`` + usage roll-up.

``tool_loop`` is the shared agentic loop extracted from the simple path and
research's per-aspect sub-agent (RFC 0015 Phase 4's "shared run_tool_loop()").
The two callers differ deliberately — streaming vs plain calls, usage policy,
final-hop tool drop, break condition — so those live in the injected ``call``
and the flags, never in divergent loop copies.

The engine's date/time line lives here as ``datetime_block``. The interpreter
keeps a compatibility wrapper so existing tests and benchmarks that monkeypatch
``interpreter._current_datetime_block`` still work.

Phase 4+ — each Lobe is a small state machine of ``LobeNode``s. The lobe's
``state_machine()`` returns the candidate nodes; ``activated_nodes(ctx)``
resolves which ones are enabled for this turn (using the flat
``flow_lobe_weights`` surface). The lobe's ``build_context`` and ``prompt``
compose from the enabled nodes — the "LLM's internal thoughts shaped by the
current context" the design calls for.

The narrow per-turn data contracts (``LlmCall``, ``LobeServices``,
``TurnContext``, ``PromptContribution``, ``LobeResult``, ``StageResult``) live
in ``agent_sdk.contracts`` so the SDK's contract layer carries no engine
dependencies; they are re-exported here for the established import path.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from agent_sdk.contracts.llm import LlmCall
from agent_sdk.contracts.services import LobeServices
from agent_sdk.contracts.turn import (
    LobeResult,
    PromptContribution,
    StageResult,
    TurnContext,
)
from agent_sdk.network.activation import ContextBound, LobeNode, LobeSpec, propagate_nodes

__all__ = [
    "LlmCall",
    "LobeServices",
    "TurnContext",
    "PromptContribution",
    "LobeResult",
    "StageResult",
    "BaseLobe",
    "Lobe",
    "extract_text",
    "datetime_block",
    "tool_loop",
    "WEEKDAY_NAMES",
]

WEEKDAY_NAMES = {
    "vi": ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}


class BaseLobe:
    """Executable lobe contract.

    Built-in lobes expose a ``LOBE`` instance that owns the activation spec,
    prompt contribution, behavior, context loading/rendering, and node
    write-back methods for that unit. Default methods are no-ops so each lobe
    implements only the parts it owns.
    """

    spec: LobeSpec

    @property
    def id(self) -> str:
        return self.spec.id

    def signals(self, ctx: dict) -> dict[str, float]:
        return self.spec.signals(ctx)

    def prompt(self, _ctx: TurnContext) -> list[PromptContribution]:
        return []

    async def load(self, _ctx: TurnContext) -> Any:
        return None

    async def run(self, _ctx: TurnContext) -> Any:
        return None

    def render(self, _value: Any, _ctx: TurnContext) -> str:
        return ""

    def nodes(self, _value: Any, _ctx: TurnContext) -> list[Any]:
        return []

    def build_context(self, _ctx: TurnContext) -> list[Any]:
        """The per-lobe context builder (Phase 2+).

        Returns the ``ContextNode``s this lobe contributes to the system prompt
        and the attention layer for this turn. The default ``[]`` keeps the
        legacy centralized context flow active — lobes opt in by returning
        a non-empty list. When ``spec.build_context=True``, the layered
        composer (Phase 3) routes this lobe's slice of the system prompt
        through this method instead of the inlined block in
        ``_build_system_prompt``.

        Design contract:
        - Pure function of ``_ctx`` (no side effects, no LLM calls, no I/O).
        - Per-node selection still happens in ``Blackboard.select_for`` under
          the lobe's per-layer budget — this method only PRODUCES the
          candidate nodes; budget trimming is the blackboard's job.
        - ``Blackboard.write_back`` rejects ``RAW_CHUNK_KINDS`` (compression
          invariant); this method must not return raw chunks.

        Phase 4+ — the default implementation composes from the lobe's
        ``state_machine()`` (the opt-in nodes). Each enabled node's
        ``produce(ctx)`` is concatenated. Subclasses can override either
        ``state_machine()`` (the recommended G6 extension seam) or
        ``build_context()`` directly.
        """
        nodes = self._enabled_state_nodes(_ctx)
        out: list[Any] = []
        for node, _resolution in nodes:
            out.extend(node.produce(_ctx))
        return out

    def state_machine(self) -> list[LobeNode]:
        """The lobe's opt-in nodes (Phase 4+).

        Returns the candidate ``LobeNode`` instances the lobe owns. Default
        is ``[]`` — subclasses override to declare their nodes. A node is
        "enabled for this turn" when its ``propagate_nodes`` resolution
        fires (signals clear threshold, no per-bot disable override).

        G6 pattern — extend by overriding this method, never by branching
        the interpreter. Adding a new skill_activate node is one
        ``LobeNode(...)`` row; the activation surface reads it.
        """
        return []

    def activated_nodes(self, ctx: TurnContext, *, weights: Mapping[str, float]) -> list[dict]:
        """The resolved per-node activations for this turn.

        A thin convenience over ``propagate_nodes`` filtered to the lobe's
        own nodes. Returns the full per-candidate trace (so callers can
        record which nodes fired and which were disabled by overlay) — not
        just the activated ones.
        """
        own = [n for n in self.state_machine() if n.lobe_id == self.id]
        if not own:
            return []
        # ``propagate_nodes`` needs a dict-shaped context. The interpreter's
        # flow signal ctx is the canonical source; callers can pass
        # ``ctx.lobe_outputs`` (where lobes publish state) or any dict.
        signal_ctx = self._signal_ctx_for(ctx)
        return propagate_nodes(own, signal_ctx, weights=dict(weights or {}))

    def _enabled_state_nodes(self, ctx: TurnContext) -> list[tuple[LobeNode, dict]]:
        """Phase 4+ helper — the (node, resolution) pairs that are enabled
        for this turn. The lobe's ``build_context`` and ``prompt`` default
        implementations compose from these. Subclasses can override
        either hook to customize the per-turn shape."""
        weights: Mapping[str, float] = (
            ctx.lobe_outputs.get("flow_lobe_weights", {})  # type: ignore[assignment]
            if hasattr(ctx, "lobe_outputs")
            else {}
        )
        if not isinstance(weights, Mapping):
            weights = {}
        resolutions = self.activated_nodes(ctx, weights=weights)
        node_by_id = {n.id: n for n in self.state_machine() if n.lobe_id == self.id}
        out: list[tuple[LobeNode, dict]] = []
        for res in resolutions:
            if not res.get("activated"):
                continue
            node = node_by_id.get(res["id"])
            if node is not None:
                out.append((node, res))
        return out

    def _signal_ctx_for(self, ctx: TurnContext) -> dict:
        """Build the small dict ``propagate_nodes`` reads from the TurnContext.

        Lives on the lobe (not the runtime) so each lobe can pick the
        signal vocabulary that matters for its nodes (skill flags, task
        state, session facts, …). The default is a minimal cross-section
        useful for the common cases; subclasses override.
        """
        return {
            "query": getattr(ctx, "query", ""),
            "stage_id": getattr(ctx, "stage_id", None),
            "active_path": getattr(ctx, "active_path", None),
            "previous_path": getattr(ctx, "previous_path", None),
            "active_lobes": set(getattr(ctx, "active_lobes", frozenset()) or ()),
            "lobe_outputs": dict(getattr(ctx, "lobe_outputs", {}) or {}),
            "session_memory": getattr(ctx, "session_memory", None),
            "memory_items": list(getattr(ctx, "memory_items", ()) or ()),
            "task_items": list(getattr(ctx, "task_items", ()) or ()),
        }


class Lobe(BaseLobe):
    """Skill-style authoring API for a lobe — one self-describing class instead of
    a ``LobeSpec`` dataclass + a separate ``signals`` function + weights + a magic
    threshold. Declare the metadata and ONE programmatic activation; ``.spec``
    compiles to the internal ``LobeSpec`` the network consumes (byte-identical at
    default weights, so parity holds).

    Mirrors a skill: ``id``/``name``/``description`` + ``use_when`` (the natural-
    language trigger — documentation AND the source for semantic/LLM activation,
    exactly as an on-demand skill activates off its description). The deterministic
    free signal is the :meth:`activation` method; cross-lobe wiring is ``excites``.
    Lobes that need multi-signal or named-tuning-key activation may still override
    :meth:`signals` / set ``signal_weights`` — the ergonomic path for the common
    single-signal case, not a cage."""

    # ── self-description: a lobe's file should answer what / when / how on its own
    id: str = ""
    name: str = ""
    description: str = ""  # WHAT it does (one line)
    use_when: str = ""  # WHEN — NL trigger: doc + semantic-activation source
    how: str = ""  # HOW it works when active (mechanism, prose)
    system_prompt: str | None = None  # prompting lobes: the system template (None = no LLM call)
    user_template: str | None = None
    layer: int = 0
    behavior: str = "recall"  # behavior CLASS: recall|decompose|compose|select|rewrite|…
    writes: tuple[str, ...] = ()
    excites: dict[str, float] = {}  # outgoing edges: lobe_id -> weight
    pinned: bool = False
    prior: float = 0.0
    threshold: float = 0.5  # min_activation
    order: int = 0
    build_context_flag: bool = False
    signal_weights: dict[str, float] = {}
    attends_kinds: tuple[str, ...] = ()

    def activation(self, ctx: dict) -> float:
        """The ONE deterministic, free activation signal (0 = dark). Reads ctx
        flags / lexical cues / session state — never an LLM call. Default 0.0
        (signal-gated dark) unless ``pinned``."""
        return 0.0

    def _activation_signals(self, ctx: dict) -> dict[str, float]:
        """Default: the activation method as a single signal named by id. Override
        :meth:`signals` for multi-signal lobes that need named tuning keys."""
        return {self.id: float(self.activation(ctx))}

    def state(self, ctx: dict) -> str:
        """context → STATE. The lobe resolves which lifecycle state this turn is in
        (the second step of a lobe's pipeline: context → state → activation →
        prompt). Default ``""`` (stateless lobe). A state-machine lobe overrides
        this to report its state (e.g. selecting / activating / driving), which the
        engine records in ``trace`` and which gates the per-state prompt nodes."""
        return ""

    def state_node(
        self,
        node_id: str,
        *,
        when: str,
        produce: Callable,
        prompt: Callable | None = None,
        desc: str = "",
        stability: str = "stable",
        order: int = 0,
        threshold: float = 0.5,
    ) -> LobeNode:
        """Declare one lifecycle STATE's prompt as a readable row: it fires ``when``
        a single ctx flag is set, and ``produce``s its context (state → prompt).
        Collapses the per-node activation boilerplate so a lobe's ``state_machine``
        reads as its actual lifecycle (selecting → activating → … → done)."""
        flag = when

        def _signals(ctx: dict, _flag: str = flag) -> dict[str, float]:
            return {_flag: 1.0 if ctx.get(_flag) else 0.0}

        return LobeNode(
            id=node_id,
            lobe_id=self.id,
            layer=self.layer,
            stability=stability,
            prior=0.0,
            signals=_signals,
            signal_weights={flag: 1.0},
            min_activation=threshold,
            order=order,
            description=desc,
            produce=produce,
            prompt=prompt or (lambda _ctx: []),
        )

    @property
    def spec(self) -> LobeSpec:
        cached = self.__dict__.get("_spec")
        if cached is not None:
            return cached
        # Empty by default — the network defaults an absent signal to weight 1.0,
        # so this matches a hand-written LobeSpec that omits signal_weights.
        weights = dict(self.signal_weights)
        spec = LobeSpec(
            id=self.id,
            behavior=self.behavior,
            layer=self.layer,
            prior=self.prior,
            pinned=self.pinned,
            attends=ContextBound(kinds=tuple(self.attends_kinds))
            if self.attends_kinds
            else ContextBound(),
            signals=self._activation_signals,
            signal_weights=weights,
            edges=dict(self.excites),
            writes=tuple(self.writes),
            min_activation=self.threshold,
            order=self.order,
            build_context=self.build_context_flag,
        )
        self.__dict__["_spec"] = spec
        return spec


def extract_text(msg: Any) -> str:
    """All text blocks of a provider message, newline-joined."""
    return "\n".join(b.text for b in msg.content if b.type == "text")


def datetime_block(tz_name: str, lang: str = "vi") -> str:
    """One date/time line for user-facing prompts."""
    import datetime as _dt
    from zoneinfo import ZoneInfo

    try:
        now = _dt.datetime.now(ZoneInfo(tz_name))
    except Exception:
        now = _dt.datetime.now(_dt.UTC)
        tz_name = "UTC"
    weekdays = WEEKDAY_NAMES.get(lang, WEEKDAY_NAMES["en"])
    return (
        f"Current date/time: {now.strftime('%Y-%m-%d %H:%M')} "
        f"({weekdays[now.weekday()]}, {tz_name}). "
        "Resolve all relative date/time references in the user's language "
        "against this."
    )


async def tool_loop(
    call: Callable[[list[dict], list[dict]], Awaitable[tuple[Any, str]]],
    *,
    messages: list[dict],
    tools: list[dict],
    execute_tools: Callable[[Any], Awaitable[list[dict]]],
    assistant_content: Callable[[Any], list[dict]],
    max_loops: int,
    drop_tools_on_final_hop: bool = False,
    strict_end_turn: bool = False,
    retier: Callable[[list[dict], int], list[dict]] | None = None,
) -> tuple[Any, str]:
    """The shared agentic loop: model call → execute tool_use blocks → repeat.

    ``call(messages, tools) -> (msg, text_hint)`` performs ONE model call —
    the caller owns streaming, partial emission, and usage policy; ``text_hint``
    is the live-streamed text ("" for non-streaming callers).
    ``execute_tools(msg)`` runs the msg's tool_use blocks and returns the
    tool_result content blocks; ``assistant_content(msg)`` rebuilds the
    assistant message's content blocks.

    Break semantics (both legacy loops preserved exactly):
    - ``strict_end_turn=True`` (simple path): only ``end_turn`` ends the loop
      and yields the answer (``text_hint`` falling back to ``extract_text``);
      any other non-tool stop reason loops again.
    - ``strict_end_turn=False`` (research): any non-``tool_use`` stop breaks;
      the caller extracts what it needs from the returned msg.
    - ``drop_tools_on_final_hop``: the final allowed hop runs WITHOUT tools so
      the model must answer from what it gathered.

    PreAct (react-context-management.md): ``retier(messages, hop)`` —
    when supplied — re-tiers the message tail AFTER each hop's observation is
    appended and BEFORE the next model call, so the prompt funnels (newest
    observation full, spent ones demoted to hints) instead of accumulating.
    ``None`` (default) ⇒ vanilla-ReAct accumulation, byte-identical to before.

    Returns ``(last_msg, answer_text)`` — ``answer_text`` is non-empty only
    for a strict end_turn exit.
    """
    msg: Any = None
    answer_text = ""
    for loop in range(max_loops):
        loop_tools = [] if (drop_tools_on_final_hop and loop >= max_loops - 1) else tools
        msg, text_hint = await call(messages, loop_tools)
        if msg.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": assistant_content(msg)})
            messages.append({"role": "user", "content": await execute_tools(msg)})
            if retier is not None:
                # Funnel the tail in place — the caller holds this same list
                # reference (pipeline/evidence state stays coherent).
                messages[:] = retier(messages, loop)
        elif strict_end_turn:
            if msg.stop_reason == "end_turn":
                answer_text = text_hint or extract_text(msg)
                break
            # Other stop reasons (e.g. max_tokens) loop again — legacy
            # simple-path semantics: never accept a truncated turn as final.
        else:
            break
    return msg, answer_text
