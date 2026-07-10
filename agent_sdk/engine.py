"""The generic PreAct turn driver (the engine kernel).

One turn = the deterministic core (recognize flow → activate lobes → resolve
stages → build the per-stage prompt) wrapped around the I/O seams (``LlmCall``,
``ToolRuntime``, ``Memory``). The engine streams typed events throughout and
assembles a ``FinalEnvelope``-shaped :class:`AgentResult` + a full :class:`Trace`.

It is a pure function of ``(network, context)`` for everything except the model
and tool calls — exactly the split that makes the core portable (docs/porting.md).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import inspect
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

from agent_sdk.clients.base import make_client
from agent_sdk.clients.messages import ProviderUsage
from agent_sdk.contracts.memo import Citation
from agent_sdk.events import (
    CitationFound,
    Final,
    MetaAction,
    PathResolved,
    RunStart,
    StageEnd,
    StageStart,
    TextDelta,
    ToolCall,
    ToolResult,
    stamp,
)
from agent_sdk.flow_def import Flow
from agent_sdk.inspection import (
    EngineSnapshot,
    FlowAxisSnapshot,
    FlowStepInspection,
    LobeAxisSnapshot,
    LobeInspection,
)
from agent_sdk.lobes.runtime import Lobe, datetime_block
from agent_sdk.metacognition.regulator import _TRIMMABLE_LOBES
from agent_sdk.metacognition_facade import PINNED_UNSKIPPABLE, Metacognition
from agent_sdk.network.activation import merge_lobe_weights, propagate, validate_network
from agent_sdk.result import AgentResult, MemoryUpdate, Refusal, Trace, Usage
from agent_sdk.session import SessionState
from agent_sdk.skills import SkillRegistry, build_skill_prompt_block
from agent_sdk.stages import Stage, StageRegistry

# XML prompt composition (default) — Claude (and Claude Code) parse XML-delimited context
# far more reliably than flat markdown, so each composed section is wrapped in a tag. A few
# sources map to Claude Code's canonical tag names; the rest use their provenance source.
_XML_TAG_MAP = {"datetime": "env", "memory_index": "memory", "session": "conversation"}
_LEAD_BRACKET_RE = re.compile(r"^\[[^\]]*\]\n")  # drop a redundant "[Header]\n" line under a tag

# Canonical prompt-layer order (docs/concepts/14-prompt-engineering.md). Best-practice
# composition: a STABLE, cacheable instruction prefix leads; the VOLATILE per-turn tail trails
# into the message hops (the conversation + query already own recency there). Segments sort by
# (stability tier, layer band, authored order), so the turn-volatile sections always form a
# contiguous suffix — the future cache-prefix boundary — and identity is never buried mid-prompt.
# An unmapped source falls in the TASK band; stability is the primary key, so a per-turn section
# stays in the tail regardless of band. New capability = a registry row, never an order branch.
(_LAYER_IDENTITY, _LAYER_DIRECTIVES, _LAYER_CAPABILITIES, _LAYER_TASK,
 _LAYER_CONTRACT, _LAYER_SAFETY, _LAYER_CONTEXT, _LAYER_ENV) = range(8)
_PROMPT_LAYERS = {
    "instructions": _LAYER_IDENTITY,
    "memory_directive": _LAYER_DIRECTIVES,
    "tools": _LAYER_CAPABILITIES, "skills": _LAYER_CAPABILITIES,
    "skill_select": _LAYER_CAPABILITIES, "skill_active": _LAYER_CAPABILITIES,
    "stage_prompt": _LAYER_TASK, "subject": _LAYER_TASK, "synthesize": _LAYER_TASK,
    "plan": _LAYER_TASK, "research": _LAYER_TASK, "condense": _LAYER_TASK,
    "classify": _LAYER_TASK, "scope_check": _LAYER_TASK, "respond": _LAYER_TASK,
    "todo_list": _LAYER_TASK, "plan_supervise": _LAYER_TASK,
    "understand": _LAYER_TASK, "explore": _LAYER_TASK, "act": _LAYER_TASK,
    "grounding": _LAYER_CONTRACT, "cite": _LAYER_CONTRACT, "format": _LAYER_CONTRACT,
    "filter": _LAYER_SAFETY,
    "memory_recall": _LAYER_CONTEXT, "session_recall": _LAYER_CONTEXT,
    "ctxvar_resolve": _LAYER_CONTEXT, "retrieved_context": _LAYER_CONTEXT,
    "memory_index": _LAYER_CONTEXT, "session": _LAYER_CONTEXT, "context": _LAYER_CONTEXT,
    "notes": _LAYER_CONTEXT, "datetime": _LAYER_ENV,
}
_STAB_RANK = {"stable": 0, "slow": 1, "turn": 2, "volatile": 3}


def _layer_key(part: tuple[str, str, str], idx: int) -> tuple[int, int, int]:
    """Sort key for one ``(source, text, stability)`` segment: stability tier first (so the
    volatile tail is contiguous), then the canonical layer band, then authored order."""
    source, _text, stability = part
    return (_STAB_RANK.get(stability, 1), _PROMPT_LAYERS.get(source, _LAYER_TASK), idx)

# Generic default prompt for a `loop="map"` sub-task (an item may override it via
# `system_prompt`). Domain-free: the engine knows "sub-tasks", not "todos".
_MAP_ITEM_PROMPT = (
    "Complete ONLY this sub-task using the available tools, then state its result "
    "concisely. Prior sub-task results are in the notes — build on them; do not redo "
    "them or start others."
)

# Per-result text kept in the turn scratchpad's fan-out results (the fan-in lobe + telemetry read
# this; the full result rides ``holder``/the answer). Small enough that a wide fan-out's results list
# stays under the scratchpad value cap — so every subagent's result survives (no zero-collapse).
_FANOUT_RESULT_CHARS = 700

# Conservative default concurrency for parallel fan-out. ``fanout_max`` caps the number of
# work-items (≤ 40); this caps how many run AT ONCE. Bursting many simultaneous provider
# calls trips rate/concurrency limits on most endpoints (observed: a wide fan-out of 7-8
# workers all failing at once), so the semaphore stays small and the rest queue. A worker
# that still fails after a serial retry is recorded ``status="failed"`` + a degraded marker.
_FANOUT_CONCURRENCY = 5

# Per-turn context for tools/runtimes that need turn state (e.g. a tool that reads or
# writes the turn scratchpad). A generic seam — the engine owns the turn; a tool opts in
# by reading ``current_turn()``. No domain knowledge here.
_TURN: contextvars.ContextVar[Any] = contextvars.ContextVar("agent_sdk_turn", default=None)


def current_turn() -> Any:
    """The active ``TurnContext`` (or None) — the seam a ToolRuntime uses to reach turn
    state (``current_turn().scratchpad``). Set by the engine for the duration of a turn."""
    return _TURN.get()


def _xml_tag(source: str) -> str:
    tag = _XML_TAG_MAP.get(source, source).lower()
    return re.sub(r"[^a-z0-9_]", "_", tag) or "section"


__all__ = ["Engine"]

_WH = ("what", "why", "how", "when", "where", "who", "which", "can ", "is ", "do ", "does ")


def _block_to_dict(block: Any) -> dict:
    """Normalize a provider content block (dataclass or anthropic obj) to a dict.

    A *thinking* block is surfaced as ``type:"thinking"`` carrying its reasoning text —
    never stringified to a ``str(block)`` Python repr. The old repr fallback both leaked
    a ``ThinkingBlock(...)`` repr into the answer (via ``_text_of`` on an echoed history)
    and corrupted replayed history (MiniMax then parrots the repr back as text)."""
    t = getattr(block, "type", None)
    if t == "text":
        return {"type": "text", "text": getattr(block, "text", "")}
    if t == "tool_use":
        return {
            "type": "tool_use",
            "id": getattr(block, "id", ""),
            "name": getattr(block, "name", ""),
            "input": getattr(block, "input", {}) or {},
        }
    if t in ("thinking", "redacted_thinking"):
        return {"type": "thinking", "text": getattr(block, "thinking", "") or ""}
    # Unknown block: keep only a genuine string ``.text``; never a repr.
    txt = getattr(block, "text", None)
    return {"type": "text", "text": txt if isinstance(txt, str) else ""}


def _assistant_content(msg: Any) -> list[dict]:
    """Assistant content for REPLAY into the running history. Thinking blocks are
    dropped — the provider does not need its own prior reasoning replayed, and
    serializing it (especially as a repr) corrupts the next hop."""
    out: list[dict] = []
    for b in getattr(msg, "content", []) or []:
        d = _block_to_dict(b)
        if d.get("type") == "thinking":
            continue
        out.append(d)
    return out


def _text_of(msg: Any) -> str:
    return "\n".join(
        getattr(b, "text", "")
        for b in getattr(msg, "content", []) or []
        if getattr(b, "type", None) == "text"
    ).strip()


def _tool_uses(msg: Any) -> list[Any]:
    return [b for b in getattr(msg, "content", []) or [] if getattr(b, "type", None) == "tool_use"]


def _response_blocks(msg: Any) -> list[dict]:
    """Assistant content as plain blocks (the viewer's per-hop ``response``)."""
    out: list[dict] = []
    for b in getattr(msg, "content", []) or []:
        t = getattr(b, "type", None)
        if t == "text" and getattr(b, "text", "").strip():
            out.append({"type": "text", "text": b.text})
        elif t == "tool_use":
            out.append({
                "type": "tool_use", "id": getattr(b, "id", ""),
                "name": getattr(b, "name", ""), "input": getattr(b, "input", {}) or {},
            })
    return out


def _call_usage(msg: Any) -> dict:
    u = getattr(msg, "usage", None)
    if u is None:
        return {}
    return {
        "input_tokens": int(getattr(u, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
    }


def _attention_rollup(flow_stages: list[dict]) -> dict:
    """Turn-level context telemetry from the per-stage traces (Phase 1).

    Flattens each stage's attention tier nodes (tagged by stage) and rolls up the
    per-stage input-token + funnel-tail series, so the viewer can render one
    Context/Funnel panel for the turn. Empty when no stage emitted nodes.
    """
    nodes: list[dict] = []
    counts = {"1": 0, "2": 0, "3": 0}
    stages: list[dict] = []
    for s in flow_stages:
        attn = s.get("attention") or {}
        meta = s.get("metadata") or {}
        for n in attn.get("nodes", []) or []:
            nodes.append({**n, "stage": s.get("stage")})
        for k, v in (attn.get("tier_counts") or {}).items():
            counts[k] = counts.get(k, 0) + v
        stages.append({
            "stage": s.get("stage"),
            "input_tokens": meta.get("input_tokens", 0),
            "funnel_obs_chars": meta.get("funnel_obs_chars", []),
            "tier_counts": attn.get("tier_counts", {}),
        })
    if not nodes and not any(st["funnel_obs_chars"] or st["input_tokens"] for st in stages):
        return {}
    return {"nodes": nodes, "tiers": nodes, "tier_counts": counts, "stages": stages}


def _obs_tail_chars(msgs: list[dict]) -> int:
    """Total chars of tool-result (observation) content in the message tail.

    The funnel's job is to keep this bounded across hops; recording it per hop
    makes "context grows O(hops)" vs "context plateaus" directly visible in the
    trace (Phase 1 telemetry). Counts only ``tool_result`` blocks — the spent
    observations the funnel re-tiers — not the stable system/tool-schema prefix.
    """
    total = 0
    for m in msgs:
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                c = block.get("content")
                total += len(c) if isinstance(c, str) else len(str(c))
    return total


def _tool_defs(specs: list[dict] | None) -> list[dict]:
    """Anthropic tool specs → the viewer's ``tools`` shape (name/description/params).

    The real ``tools`` payload sent to the model that hop — surfaced in the Prompt
    panel's "tools offered" section (grouped by area, flagged when called)."""
    out: list[dict] = []
    for s in specs or []:
        schema = s.get("input_schema") or {}
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        out.append({
            "name": s.get("name"),
            "description": s.get("description", ""),
            "params": [{"name": k, "required": k in required} for k in props],
        })
    return out


class Engine:
    def __init__(
        self,
        *,
        client: Any,
        lobes: list[Lobe],
        stages: list[Stage],
        flows: list[Flow],
        paths: list[Any] | None = None,
        skills: list[Any] | None = None,
        tools: Any = None,
        instructions: str = "",
        system_addendum: str = "",
        weights: dict | None = None,
        budgets: dict | None = None,
        metacognition: Any = None,
        memory: Any = None,
        memory_runtime: Any = None,
        memory_store: Any = None,
        embed: Any = None,
        require_citations: bool = False,
        refusal_message: str | None = None,
        share_history: bool = False,
        tools_in_prompt: bool = False,
        funnel: bool = False,
        tz: str = "UTC",
        lang: str = "en",
        prompt_format: str = "xml",
        context: Any = None,
        pre_turn_gate: Any = None,
        max_hops: int = 6,
        default_max_tokens: int = 4096,  # thinking models burn >1k on reasoning before a tool call
    ):
        self.client = make_client(client)
        self.lobes = list(lobes)
        # Reply flow: ensure a dedicated `respond` lobe exists (network-agnostic). The engine
        # pins it onto the terminal stage so that stage becomes the response stage — it renders
        # the next conversation message from the gathered notes. No extra LLM call.
        if not any(lb.id == "respond" for lb in self.lobes):
            from agent_sdk.expression.lobes.respond import LOBE as _respond_lobe

            self.lobes.append(_respond_lobe)
        # Weave lobes into canonical (layer, order) position so extension-plugin lobes land
        # where they belong in the forward-DAG regardless of contribution order. Stable sort:
        # equal (layer, order) keeps contribution order (byte-identical for the default network).
        self.lobes.sort(key=lambda lb: (lb.spec.layer, lb.spec.order))
        self.lobe_by_id = {lb.id: lb for lb in self.lobes}
        self.lobe_specs = [lb.spec for lb in self.lobes]
        validate_network(self.lobe_specs)
        self.stage_registry = StageRegistry(stages)
        self.flows = list(flows)
        self.flow_by_id = {f.id: f for f in self.flows}
        self.skill_packs = [s.to_pack() for s in (skills or [])]
        self.skill_registry = SkillRegistry(self.skill_packs)
        self.tools = tools
        # On-demand skills need a way for the model to LOAD them: the skill-activation
        # tools (ActivateSkill / skill.read / skill.search). Compose them in whenever an
        # on-demand skill is declared so the directive in the prompt is actually callable.
        # Eager skills inline their body and need no tool.
        on_demand_slugs = [
            p.id for p in self.skill_packs if getattr(p, "injection", "") == "on_demand"
        ]
        self._skill_runtime = None
        self._skill_tool_names: set[str] = set()
        if on_demand_slugs:
            from agent_sdk.contracts.tools import CompositeToolRuntime
            from agent_sdk.skills import ACTIVATE, READ, SEARCH, SkillToolRuntime
            from agent_sdk.skills.cache import SurfaceCache

            # Lazy compile-on-activate: the runtime builds each skill's budget surface
            # with the engine's client the first time it's activated, and caches it.
            _b = budgets or {}
            self._skill_runtime = SkillToolRuntime(
                self.skill_registry, on_demand_slugs,
                llm=self.client,
                cache=SurfaceCache(persist=bool(_b.get("skill_surface_persist", True))),
                budget_tokens=int(_b.get("skill_surface_budget", 600)),
                # Default per the skillbench A/B (benchmarks/skillbench/compare.py): the
                # deterministic chunk-index surface is the most compact activation at
                # equal accuracy and zero compile cost — it dominates both "off" (raw
                # body) and "llm" (compile cost, no accuracy gain). "llm" stays opt-in.
                surface_mode=str(_b.get("skill_surface_mode", "deterministic")),
            )
            self._skill_tool_names = {ACTIVATE, READ, SEARCH}
            runtimes = [self._skill_runtime]
            if self.tools is not None:
                runtimes.append(self.tools)
            self.tools = CompositeToolRuntime(runtimes)
        self.instructions = instructions
        # An SDK-injected system directive (e.g. the memory directive) — part of the composed prompt
        # but NOT part of `instructions` (so the spec / with_() keep the user's original).
        self.system_addendum = system_addendum
        self.weights = dict(weights or {})
        self.budgets = dict(budgets or {})
        self.metacognition = Metacognition.coerce(metacognition)
        self.memory = memory
        self.memory_runtime = memory_runtime
        self.memory_store = memory_store
        self.embed = embed
        # Plugin deep-hooks (populated by PreactAgent from AgentSetup).
        self._prefetch_hooks: list[Any] = []
        self._tool_filters: list[Any] = []
        self._turn_hooks: list[Any] = []
        # Grounding/citation seams owned by a plugin (RagPlugin), not the core.
        self._finalize_hooks: list[Any] = []
        self._tool_result_hooks: list[Any] = []
        self.require_citations = require_citations
        self.refusal_message = refusal_message
        self.share_history = share_history
        self.tools_in_prompt = tools_in_prompt
        self.funnel = funnel
        self.tz = tz
        self.lang = lang
        self.prompt_format = prompt_format  # "xml" (default, Claude-Code-style) | "markdown"
        # Opaque host context bag (identity/principal/tenant/channel) folded into
        # the per-turn snapshot so it lands on every ``TurnContext.identity`` /
        # ``.channel``. The engine never inspects it — a host ToolRuntime / lobe /
        # tool_filter reads ``current_turn().identity`` for ACL etc. A prefetch
        # hook may still override a key. None ⇒ unchanged (empty identity/channel).
        self.context = dict(context) if context else {}
        # Pre-turn gate (host seam): a callable ``(query, state) -> AgentResult |
        # None`` (sync or async) run BEFORE any reasoning. A non-None result ends
        # the turn immediately (a golden-answer cache hit or a refusal-rule match);
        # None proceeds. The host closure carries whatever it needs (identity/ACL
        # cohort via ``self.context``, golden head, refusal rules). None ⇒ no gate.
        self._pre_turn_gate = pre_turn_gate
        self.max_hops = max_hops
        self.default_max_tokens = default_max_tokens
        # Explicit ported PathSpec recognizers (production network) take
        # precedence; otherwise derive them from the façade flows.
        self.path_specs = list(paths) if paths else self._build_path_specs()

    # ── deterministic core wiring ────────────────────────────────────────────
    def _build_path_specs(self) -> list:
        specs = []
        for f in self.flows:
            members: list[str] = []
            for st in self.stage_registry.resolve(f.stages):
                members.extend(st.lobes)
            members = list(dict.fromkeys(members))
            bias = {m: 1.0 for m in members}
            specs.append(f.to_path_spec(members, bias=bias))
        return specs

    def build_context(self, query: str, state: SessionState) -> dict:
        q = query.strip()
        low = q.lower()
        ctx = {
            "query": query,
            "is_question": q.endswith("?") or low.startswith(_WH),
            "word_count": len(q.split()),
            "has_history": bool(state.history),
            "ambiguous": False,
        }
        # Fold the host's per-turn recognition FLAGS into the ctx so domain path
        # recognizers can read them (e.g. ``config_mode``/``relearn_active`` for the
        # admin steward paths). Only scalar top-level keys the host set on the
        # context bag — the engine moves opaque values, no domain knowledge.
        if isinstance(self.context, dict):
            for k, v in self.context.items():
                if k not in ctx and isinstance(v, (bool, int, float, str)):
                    ctx[k] = v
        # Fold a metacognition flow bias the meta_control tool recorded on a PRIOR turn
        # into the recognition ctx as a deterministic flag (``meta_flow_bias_<path>``) a
        # plugin path recognizer can read. No-op when unset ⇒ default routing unchanged.
        bias = getattr(state, "meta_flow_bias", "")
        if bias:
            ctx[f"meta_flow_bias_{bias}"] = True
        return ctx

    def _policy(self) -> dict:
        return {
            "capabilities": {"skills": [p.id for p in self.skill_packs]},
            "skill_strategy": "static",
        }

    # Universal-memory tools are essentials: a digest must always be re-expandable, so they
    # bypass the per-stage allowlist (and the adaptive drop) in every agentic stage.
    _MEMORY_ESSENTIALS = ("recall", "note")

    def _skill_tools_live(self, stage: Stage) -> bool:
        """Whether the skill-activation tools should be exposed on this stage: only
        when an on-demand skill is declared FOR this stage, or one is already in use
        (so grounding/format/other stages with no skill don't carry ~155 tok of
        unused skill tool specs)."""
        if not self._skill_tool_names:
            return False
        in_use = list((getattr(current_turn(), "lobe_outputs", {}) or {}).get("skills_in_use") or [])
        if in_use:
            return True
        declared = self.skill_registry.active_for_stage(self._policy(), stage.id)
        return any(getattr(p, "injection", "") == "on_demand" for p in declared)

    def _tool_specs(self, stage: Stage) -> list[dict]:
        if self.tools is None:
            return []
        specs = self.tools.get_tool_specs()
        if stage.tools:
            allow = set(stage.tools)
            if self.memory_store is not None:
                allow |= set(self._MEMORY_ESSENTIALS)
            # Skill-activation tools bypass the per-stage allowlist, but only when a
            # skill is actually loadable/active here (mirrors the memory essentials).
            if self._skill_tools_live(stage):
                allow |= self._skill_tool_names
            specs = [s for s in specs if s.get("name") in allow]
        return specs

    def _select_tools(self, stage: Stage, query: str) -> tuple[list[dict], dict]:
        """Adaptive tool exposure (Phase 4) — route the stage's tools to
        inject/hint/drop by relevance to the turn, under a token budget, with an
        essentials guard (the stage allowlist + ``memory`` are never dropped).

        Computed ONCE per stage (tools are a stage property, not a hop property),
        so the cached prompt prefix stays byte-stable across the stage's hops.
        Default — ``tool_strategy != "adaptive"`` or no ``tool_budget_tokens`` —
        returns the static specs unchanged (byte-identical, all tools callable).
        """
        specs = self._tool_specs(stage)
        strategy = self.budgets.get("tool_strategy", self._policy().get("tool_strategy", "static"))
        budget = self.budgets.get("tool_budget_tokens")
        if strategy != "adaptive" or not budget or len(specs) <= 1:
            return specs, {}

        from agent_sdk.network.context_builder import DEFAULT_NODE_WEIGHTS
        from agent_sdk.selection import select_with_hints

        essentials = set(stage.tools or ()) | {"memory"}
        if self.memory_store is not None:
            essentials |= set(self._MEMORY_ESSENTIALS)
        sel = select_with_hints(
            specs, query,
            key=lambda s: s.get("name", ""),
            text=lambda s: f"{s.get('name', '')} {s.get('description', '')}",
            weights=self.weights or None,
            inject_threshold=DEFAULT_NODE_WEIGHTS["tier_inject_threshold"],
            hint_threshold=DEFAULT_NODE_WEIGHTS["tier_hint_threshold"],
            budget_tokens=int(budget),
            essentials=tuple(essentials),
            embed_one=self._embed_one_cb(),
        )
        keep = {x.key for x in sel if x.tier in ("inject", "hint")}
        record = {
            "kept": [x.key for x in sel if x.tier == "inject"],
            "hinted": [x.key for x in sel if x.tier == "hint"],
            "dropped": [x.key for x in sel if x.tier == "drop"],
        }
        # Keep payload in the original spec order; a dropped tool is removed from
        # this stage entirely (the essentials guard ensures it was non-essential).
        payload = [s for s in specs if s.get("name") in keep]
        return payload, record

    def _tools_prompt_block(self, stage: Stage, specs: list[dict] | None = None) -> str:
        """The stage's tools rendered as a prompt section (name(params) — desc).

        Lets the tool definitions live INSIDE the system prompt (a colored
        ``tools`` provenance section) rather than only in the separate payload
        list. They are still also sent via the native ``tools`` param. ``specs``
        overrides the static set (Phase 4 adaptive selection passes the chosen
        subset so the prompt matches the payload)."""
        specs = self._tool_specs(stage) if specs is None else specs
        if not specs:
            return ""
        lines = ["Tools available this step — call them as needed:"]
        for s in specs:
            schema = s.get("input_schema") or {}
            props = schema.get("properties") or {}
            required = set(schema.get("required") or [])
            params = ", ".join(k + ("*" if k in required else "") for k in props)
            desc = (s.get("description", "") or "").strip().splitlines()[0] if s.get("description") else ""
            lines.append(f"- {s.get('name')}({params})" + (f" — {desc}" if desc else ""))
        return "\n".join(lines)

    def _compose_system(
        self, stage: Stage, ctx: dict, state: SessionState, notes: list[str],
        *, is_last: bool = False,
    ) -> str:
        return self._compose_system_segmented(stage, ctx, state, notes, is_last=is_last)[0]

    def _compose_system_segmented(
        self, stage: Stage, ctx: dict, state: SessionState, notes: list[str],
        turn_ctx: Any = None, attn_out: dict | None = None,
        tool_specs: list[dict] | None = None, skill_sel_out: list | None = None,
        *, is_last: bool = False,
    ) -> tuple[str, list[dict]]:
        """Compose the stage system prompt AND its provenance segments.

        Each contributing block is tagged by its source — a **lobe id** for a
        lobe's prompt contribution (so the Prompt panel can colour the text by
        the lobe that produced it), or a section name (``instructions`` /
        ``skills`` / ``session`` / ``context`` / ``notes`` / ``grounding`` /
        ``datetime``) for engine-composed regions. The joined text is identical
        to ``_compose_system``; ``segments`` carries ``{source, start, end,
        stability}`` offset ranges over it.
        """
        # (source, text, stability)
        parts: list[tuple[str, str, str]] = []
        if self.instructions:
            parts.append(("instructions", self.instructions.strip(), "stable"))
        if self.system_addendum:
            parts.append(("memory_directive", self.system_addendum.strip(), "stable"))
        # Per-stage system_prompt override (used by per-todo sub-stages to carry that
        # todo's tailored instruction). Lobe contributions still append below.
        if getattr(stage, "system_prompt", None):
            parts.append(("stage_prompt", stage.system_prompt.strip(), "stable"))
        # The state's subject — the specific sub-question/aspect this instance works on (set by
        # the dynamic state plan when a state is expanded over subjects; None ⇒ whole turn).
        _subj = getattr(stage, "subject", None)
        if _subj:
            parts.append(("subject", f"Work on this specifically:\n{str(_subj).strip()}", "stable"))
        # KB prefetch context (host-rendered): the strong-retrieval chunks seeded
        # before reasoning, surfaced so a grounding stage answers from them. Opaque
        # to the engine; rendered by the host plugin (kept domain-free here).
        _pf = getattr(turn_ctx, "prefetch_context", "") if turn_ctx is not None else ""
        if _pf:
            parts.append(("retrieved_context", _pf.strip(), "turn"))
        # Always-on memory index: the agent SEES what it has stored each turn (the Tier-2 menu over
        # universal memory), so it answers from memory and knows what to recall — instead of saying
        # "I don't have that". Query-scored, capped, newest-first; empty store ⇒ nothing added.
        if self.memory_store is not None:
            idx = self.memory_store.render_index(query=ctx.get("query"), budget_tokens=700)
            if "\n" in idx:  # has entries beyond the header line
                parts.append(("memory_index", idx.strip(), "turn"))
        # Each lobe contributes one OR MORE prompt chunks, each its own master-prompt section.
        # A lobe's ``prompt(ctx) -> list[PromptContribution]`` is the rich path (multiple
        # source-tagged, stage-filtered, stability-typed chunks the engine assembles into
        # sections); it falls back to the static ``system_prompt`` for the simplest lobes (and
        # when no per-turn context is available).
        if turn_ctx is not None:
            turn_ctx.stage_id = stage.id
        for lobe_id in stage.lobes:
            lobe = self.lobe_by_id.get(lobe_id)
            if lobe is None:
                continue
            contribs = []
            if turn_ctx is not None:
                with contextlib.suppress(Exception):
                    contribs = lobe.prompt(turn_ctx) or []
            if contribs:
                for c in contribs:
                    sids = getattr(c, "stage_ids", ()) or ()
                    if sids and stage.id not in sids and getattr(stage, "name", "") not in sids:
                        continue
                    text = (getattr(c, "text", "") or "").strip()
                    if text:
                        parts.append((getattr(c, "source", "") or lobe_id, text,
                                      getattr(c, "stability", "stable")))
            else:
                sp = getattr(lobe, "system_prompt", None)
                if sp:
                    parts.append((lobe_id, sp.strip(), "stable"))
        if self.tools_in_prompt:
            tools_block = self._tools_prompt_block(stage, tool_specs)
            if tools_block:
                parts.append(("tools", tools_block, "slow"))
        # The skill_select lobe owns the index (state-aware) when it's in the stage;
        # only fall back to the direct block on stages that don't include it — so
        # there's never a duplicate, and a custom stage without the lobe still lists.
        if "skill_select" not in stage.lobes:
            in_use = list((getattr(turn_ctx, "lobe_outputs", {}) or {}).get("skills_in_use") or [])
            skill_block = build_skill_prompt_block(
                self.skill_registry, self._policy(), stage.id, query=ctx.get("query"),
                ranking_out=skill_sel_out, skills_in_use=in_use,
            )
            if skill_block:
                parts.append(("skills", skill_block, "slow"))
        # Dynamic lobe context (Phase 0): the stage's lobes emit ContextNodes
        # (memory/skill/task recall, …); pool → attention → tier → render. No-op
        # for prompt-only lobes (build_context returns []).
        if turn_ctx is not None:
            from agent_sdk.engine_context import collect_nodes, select_and_render

            turn_ctx.stage_id = stage.id
            producer_nodes = collect_nodes(tuple(stage.lobes), self.lobe_by_id, turn_ctx)
            budget = int(self.budgets.get("context_tokens", 8000))
            node_parts = select_and_render(
                producer_nodes, ctx.get("query", ""), q_vec=None,
                weights=self.weights or None, budget_tokens=budget,
                embed_batch=self._embed_batch(), trace_out=attn_out,
            )
            parts.extend(node_parts)
        if state.summary:
            parts.append(("session", f"[Conversation so far]\n{state.summary}", "slow"))
        if state.context:
            parts.append(("context", "[Context]\n" + "\n".join(state.context), "slow"))
        if notes:
            parts.append(("notes", "[Notes gathered this turn]\n" + "\n".join(notes), "turn"))
        if self.require_citations and stage.id == "synthesize":
            parts.append((
                "grounding",
                "Ground every factual claim in the gathered sources. If the sources "
                "do not support an answer, say you cannot confirm it from them.",
                "stable",
            ))
        # Reply flow: make the terminal stage the RESPONSE stage. If the flow already lists a
        # real `respond` stage (its lobes include "respond"), that stage renders it via the lobe
        # loop above. Otherwise pin the response lobe's framing onto whatever the terminal is
        # (no extra LLM call) — placed after the notes it refers to.
        if is_last and "respond" not in stage.lobes:
            respond = self.lobe_by_id.get("respond")
            sp = getattr(respond, "system_prompt", None) if respond else None
            if sp:
                parts.append(("respond", sp.strip(), "stable"))
        parts.append(("datetime", datetime_block(self.tz, self.lang), "turn"))

        # Canonical layer order: a stable instruction prefix leads; the turn-volatile tail trails
        # into the message hops (which own recency). Stable-sorted, so authored order holds within
        # a band and the volatile sections are always a contiguous suffix. See _PROMPT_LAYERS.
        parts = [p for _, p in sorted(enumerate(parts), key=lambda ip: _layer_key(ip[1], ip[0]))]

        sep = "\n\n"
        xml = self.prompt_format == "xml"
        out = ""
        segments: list[dict] = []
        for source, frag, stability in parts:
            if not frag:
                continue
            if xml:
                # Wrap each section in an XML tag (Claude-Code-style); drop a now-redundant
                # leading "[Header]" line since the tag names the section. Per-line note
                # prefixes ("[plan] …") are preserved.
                tag = _xml_tag(source)
                frag = f"<{tag}>\n{_LEAD_BRACKET_RE.sub('', frag)}\n</{tag}>"
            if out:
                out += sep
            start = len(out)
            out += frag
            segments.append({"source": source, "start": start, "end": len(out),
                             "stability": stability})
        return out, segments

    # ── the turn ─────────────────────────────────────────────────────────────
    async def stream(self, query: str, state: SessionState | None = None) -> AsyncIterator[Any]:
        state = state or SessionState()
        trace_id = uuid.uuid4().hex[:16]
        usage_before = self._usage_snapshot()
        # Flash (turn-scope) memory is the turn's working scratch — clear it; long-term persists.
        if self.memory_store is not None:
            self.memory_store.reset_flash()
        yield stamp(RunStart(), trace_id)

        # Pre-turn gate: a host short-circuit before any reasoning (golden-cache
        # hit / refusal-rule match). A returned AgentResult ends the turn; None
        # proceeds. Keeps the gate's host coupling (golden head, ACL-keyed cache)
        # OUT of the engine — it's an opaque callable.
        if self._pre_turn_gate is not None:
            gated = self._pre_turn_gate(query, state)
            if hasattr(gated, "__await__"):
                gated = await gated
            if gated is not None:
                yield stamp(Final(result=gated), trace_id)
                return

        ctx = self.build_context(query, state)
        weights = merge_lobe_weights({}, self.weights)
        resolution = propagate(
            self.lobe_specs, ctx, weights=weights, paths=self.path_specs, min_activation=0.0
        )
        path = resolution.path
        yield stamp(
            PathResolved(path=path.get("name", "emergent"), score=path.get("score", 0.0)), trace_id
        )

        flow = self._select_flow(path)
        stages = [
            s for s in self.stage_registry.resolve(flow.stages) if s.signal(ctx) >= s.threshold
        ]

        # Per-turn working state + context (the Phase-0 dynamic-context pipeline).
        from agent_sdk.engine_context import build_turn_context
        from agent_sdk.memory.scratchpad import Scratchpad

        scratchpad = Scratchpad()
        snapshot = await self._prefetch(query, state, path=path.get("name"))
        # Retrieval-gated refusal: a prefetch hook may decide (with retrieval in
        # hand) that the turn is out-of-scope and request a refusal via
        # ``_refuse`` — so an in-domain question with strong retrieval is NEVER
        # refused, while an off-topic one that retrieves nothing relevant is.
        # Domain-free: the engine just honors the opaque reason string.
        if isinstance(snapshot, dict) and snapshot.get("_refuse"):
            _reason = str(snapshot["_refuse"])
            _res = AgentResult(
                text=_reason, status="refused",
                refusal=Refusal(reason="policy_violation", message=_reason),
            )
            yield stamp(Final(result=_res), trace_id)
            return
        # Fold the static host context bag in as the base (identity/channel/…); a
        # prefetch hook that set the same key wins (host can override per turn).
        if self.context:
            snapshot = {**self.context, **snapshot}
        for hook in self._turn_hooks:
            with contextlib.suppress(Exception):
                hook(scratchpad)
        turn_ctx = build_turn_context(
            query, state, scratchpad=scratchpad, snapshot=snapshot,
            path=path.get("name"), active_lobes=tuple(resolution.activated),
            policy=self._policy(),
            # Seed the skills the conversation already activated so the skill_active
            # lobe keeps driving a loaded SOP this turn (RFC 0013 lifecycle). The
            # ActivateSkill tool appends to this list mid-turn; it is persisted back
            # to the session at the end of the turn.
            # Seed the registry too, so skill_select/skill_active actually FIRE
            # (without it active_skill_packs() is empty and they stay dormant). They
            # own the state-aware skill prompts: skill_select renders the index when
            # selecting, skill_active the drive-guide + pinned context_vars when driving.
            lobe_outputs={
                "skills_in_use": list(getattr(state, "skills_in_use", []) or []),
                "skill_registry": self.skill_registry,
            }
            if self.skill_packs
            else None,
        )
        # Expose the turn to tools/runtimes that opt into turn state (generic seam).
        _TURN.set(turn_ctx)

        # The turn's shared evidence channel — one pool threaded into every
        # ``call_tool`` so a KB-style runtime accumulates the chunks it retrieves
        # (and dedupes via ``already_read``) across stages/hops, and a grounding
        # lobe reads them via ``current_turn()``. Empty/ignored without KB tools.
        retrieved_chunks: list[dict] = []
        already_read: set[str] = set()
        turn_ctx.retrieved_chunks = retrieved_chunks
        turn_ctx.already_read = already_read
        # Turn-level infra-degradation markers a host tool appends via current_turn().
        degraded: list[str] = []
        turn_ctx.degraded = degraded
        # KB prefetch grounding seed: a prefetch hook (host plugin) may have run a
        # strong deterministic retrieval on the standalone query and returned the
        # chunks (``_prefetch_chunks``) + a host-rendered prompt block
        # (``_prefetch_block``) in the snapshot. Seed the chunks into the evidence
        # channel (so citation grounding resolves) and stash the rendered block on
        # the turn so the answer stage SEES them — a single-hop qna is then grounded
        # by construction even when the model's own searches are weak. Domain-free:
        # the engine moves opaque chunk dicts + an opaque string, no KB logic.
        if isinstance(snapshot, dict) and snapshot.get("_prefetch_chunks"):
            for _ch in snapshot["_prefetch_chunks"]:
                _cid = str(_ch.get("chunk_id") or "")
                if _cid and _cid not in already_read:
                    already_read.add(_cid)
                    retrieved_chunks.append(_ch)
            _blk = snapshot.get("_prefetch_block")
            if _blk:
                turn_ctx.prefetch_context = str(_blk)

        base_msgs = state.messages() + [{"role": "user", "content": query}]
        # ``share_history`` threads the running message + tool history across
        # stages so a later stage sees what earlier stages actually did (read,
        # edited, ran) instead of re-discovering it. Default off preserves the
        # compression-invariant design (stages talk through compact notes only).
        running_msgs = list(base_msgs)
        notes: list[str] = []
        citations: list[Citation] = []
        emitted_cite_ids: set[str] = set()  # CitationFound already streamed (avoid dup post-finalize)
        flow_stages_trace: list[dict] = []
        meta_actions: list[dict] = []
        llm_calls: list[dict] = []
        answer = ""

        # ── Metacognition: the OX/OY snapshots the kernel monitor reads ──────────
        # Previously the controller ran blind (snapshots=None). Build the lobe-axis +
        # engine snapshots once per turn from already-resolved state (pure reads); the
        # per-stage flow-axis is built inside the loop. Default mode is ``observe`` —
        # nothing is applied — so a no-plugin agent stays byte-identical (parity).
        meta_lobe_axis = LobeAxisSnapshot(
            lobes=[
                LobeInspection(id=e["id"], layer=e["layer"], activated=bool(e["activated"]))
                for e in resolution.lobes
            ],
            activated=list(resolution.activated),
        )
        meta_engine_snap = EngineSnapshot(
            path=path,
            flow={"name": flow.id},
            lobes=[{"id": e["id"]} for e in resolution.lobes],
        )

        # ── Phase cursor (Navigator layer) ──────────────────────────────────────
        # The cursor is a MOVABLE index, not a for-loop: a post-stage Navigator may
        # advance (default), redo a phase (bounded), or goto another phase. With no
        # navigation it walks 0..N-1 exactly like the old linear loop (parity), since
        # the nav moves are apply-gated and off by default.
        REDO_BUDGET = 1
        TOTAL_CAP = 2 * len(stages) + 2
        runs: dict[str, int] = {}  # phase id → times executed (redo budget)
        phase_ckpt: dict[str, tuple[int, int, int]] = {}  # id → (msgs,notes,cites) before 1st run
        total_runs = 0
        i = 0
        while 0 <= i < len(stages) and total_runs < TOTAL_CAP:
            stage_index, stage = i, stages[i]
            is_last_stage = stage_index == len(stages) - 1
            # Checkpoint conversational state before a phase's FIRST run so a later
            # redo / backward-goto can rewind to it (no duplicated history or notes).
            if stage.id not in phase_ckpt:
                phase_ckpt[stage.id] = (len(running_msgs), len(notes), len(citations))
            yield stamp(StageStart(flow=flow.id, stage=stage.id), trace_id)
            if self.share_history and stage_index > 0:
                # Keep user/assistant alternation across stage boundaries.
                running_msgs.append(
                    {"role": "user", "content": f"Next step ({stage.id}): "
                     f"{stage.description or stage.id}"}
                )
            # The current step as a flow-axis snapshot (scoped to this stage so the
            # monitor's observations + the regulator's decision target THIS step).
            flow_axis = FlowAxisSnapshot(
                flow=flow.id,
                disabled=False,
                steps=[
                    FlowStepInspection(
                        flow=flow.id, step=stage.id, loop=stage.loop,
                        tools=list(stage.tools), lobes=list(stage.lobes),
                    )
                ],
            )
            decision = self.metacognition.plan_next(
                lobe_axis=meta_lobe_axis, flow_axis=flow_axis, engine=meta_engine_snap,
                target_flow=flow.id, target_step=stage.id, current_lobes=tuple(stage.lobes),
            )
            # The mirror: surface the kernel's observations to the meta-context lobe
            # (harmless when the MetacognitionPlugin is not installed — nothing reads it).
            turn_ctx.lobe_outputs["meta_observations"] = [
                o.to_payload() for o in decision.observations
            ]
            if decision.action != "continue":
                meta_actions.append(
                    {"action": decision.action, "reason": decision.reason, "stage": stage.id}
                )
                yield stamp(MetaAction(action=decision.action, reason=decision.reason), trace_id)
                if (
                    decision.action == "skip_step"
                    and self.metacognition.should_apply("skip_step")
                    and not self._stage_carries_pinned(stage)
                ):
                    flow_stages_trace.append(
                        {"flow": flow.id, "stage": stage.id, "skipped": True, "steps": []}
                    )
                    yield stamp(StageEnd(flow=flow.id, stage=stage.id), trace_id)
                    i += 1  # cursor: a skipped phase advances (never redone)
                    total_runs += 1
                    continue
                if (
                    decision.action == "adjust_lobe_slice"
                    and decision.target_lobes
                    and self.metacognition.should_apply("adjust_lobe_slice")
                ):
                    # Trim the consulted lobe slice for this step (the regulator never
                    # trims cite/filter). Run the step against the narrowed slice.
                    stage = self._scoped_stage(stage, lobes=list(decision.target_lobes))

            # The meta-control tool (run on an earlier reflect step) may have requested
            # trim/skip on this step; honor it — apply-gated, pin-guarded, one-shot.
            if (
                getattr(turn_ctx, "scratchpad", None) is not None
                and (req := turn_ctx.scratchpad.get("meta_regulate_request"))
                and isinstance(req, dict)
                and req.get("step") in (None, stage.id)
            ):
                kind = req.get("request")
                turn_ctx.scratchpad.set("meta_regulate_request", None)  # one-shot
                if (
                    kind == "skip"
                    and self.metacognition.should_apply("skip_step")
                    and not self._stage_carries_pinned(stage)
                ):
                    meta_actions.append(
                        {"action": "skip_step", "reason": "meta_control regulate request",
                         "stage": stage.id}
                    )
                    yield stamp(
                        MetaAction(action="skip_step", reason="meta_control request"), trace_id
                    )
                    flow_stages_trace.append(
                        {"flow": flow.id, "stage": stage.id, "skipped": True, "steps": []}
                    )
                    yield stamp(StageEnd(flow=flow.id, stage=stage.id), trace_id)
                    i += 1  # cursor: a skipped phase advances (never redone)
                    total_runs += 1
                    continue
                if kind == "trim" and self.metacognition.should_apply("adjust_lobe_slice"):
                    narrowed = [lb for lb in stage.lobes if lb not in _TRIMMABLE_LOBES]
                    if narrowed and len(narrowed) != len(stage.lobes):
                        meta_actions.append(
                            {"action": "adjust_lobe_slice",
                             "reason": "meta_control regulate request", "stage": stage.id}
                        )
                        stage = self._scoped_stage(stage, lobes=narrowed)

            # Adaptive tool exposure (Phase 4): choose the stage's tool subset once
            # (cache-stable across hops); default is the full static set.
            sel_specs, tool_sel = self._select_tools(stage, ctx.get("query", ""))
            attn_trace: dict = {}
            skill_sel: list[dict] = []
            system, system_segments = self._compose_system_segmented(
                stage, ctx, state, notes, turn_ctx, attn_out=attn_trace,
                tool_specs=sel_specs, skill_sel_out=skill_sel, is_last=is_last_stage,
            )
            # When the skill_select lobe owns the index, it records the ranking on
            # the turn (skill_ranking); use that for the inspector. Else the direct
            # fallback filled skill_sel.
            if "skill_select" in stage.lobes:
                skill_sel = list(turn_ctx.lobe_outputs.get("skill_ranking") or [])
            steps: list[dict] = []
            stage_text = ""
            calls_before = len(llm_calls)
            stage_holder: dict[str, Any] | None = None

            stage_msgs = running_msgs if self.share_history else list(base_msgs)

            if stage.loop == "none":
                pass
            elif stage.loop in ("single",):
                resp = await self._call(stage, system, stage_msgs)
                stage_text = _text_of(resp)
                # Hedge retry (opt-in, host-driven): if the one-shot answer hedges
                # despite a seeded evidence channel, retry ONCE with a host-provided
                # forced-answer directive. The host owns the hedge detection +
                # directive text (domain); the engine just re-calls and keeps the
                # new answer only if it stops hedging. Default off ⇒ no-op.
                _retry = getattr(self, "_answer_retry", None)
                if _retry is not None and retrieved_chunks and stage_text:
                    _dir = _retry(stage_text)
                    if _dir:
                        resp2 = await self._call(
                            stage, system + "\n\n" + str(_dir), stage_msgs)
                        _t2 = _text_of(resp2)
                        if _t2 and not _retry(_t2):
                            stage_text, resp = _t2, resp2
                        elif is_last_stage:
                            # Still hedging after the forced-answer retry on the
                            # final grounding stage ⇒ the evidence has no real
                            # answer. Emit a clean REFUSAL instead of a kind=answer
                            # hedge (which scores as a false answer / FP on an
                            # unanswerable turn). Domain-free: host decided "hedge".
                            _res = AgentResult(
                                text=stage_text, status="refused",
                                refusal=Refusal(reason="no_grounding",
                                                message=stage_text),
                            )
                            yield stamp(Final(result=_res), trace_id)
                            return
                steps.append({"kind": "answer", "text": stage_text})
                # One-shot grounding (resolving [chunk_id] mentions against the
                # prefetch-seeded evidence channel) is the RagPlugin's job now — it
                # runs in the finalize hook over the final answer, so a single-loop
                # stage carries no citation logic in the engine core.
                llm_calls.append({
                    "stage": stage.id, "hop": 0,
                    "stop_reason": getattr(resp, "stop_reason", "end_turn"),
                    "usage": _call_usage(resp), "response": _response_blocks(resp),
                    "tool_results": [], "system": system, "messages": list(stage_msgs),
                    "model": self._model_label(stage),
                    "temperature": stage.temperature if stage.temperature is not None else 0.0,
                    "max_tokens": stage.max_tokens or self.default_max_tokens,
                    "tool_count": 0, "tools": [],
                })
                if self.share_history and stage_text:
                    running_msgs.append({"role": "assistant", "content": stage_text})
                if stage_text:
                    yield stamp(TextDelta(text=stage_text), trace_id)
            else:  # agentic / map
                holder: dict[str, Any] = {"text": "", "tool_calls": [], "citations": [],
                                          "llm_calls": [], "funnel_obs_chars": []}
                stage_holder = holder
                if stage.loop == "map":
                    # Generic fan-out: one scoped sub-execution per work-item in
                    # scratchpad[fanout_key]. Domain-free — a plugin fills the list.
                    gen = self._map_stage(
                        stage, system, stage_msgs, trace_id, steps, holder,
                        ctx=ctx, state=state, notes=notes, turn_ctx=turn_ctx, sel_specs=sel_specs,
                        retrieved_chunks=retrieved_chunks, already_read=already_read,
                    )
                else:
                    gen = self._agentic(
                        stage, system, stage_msgs, trace_id, steps, holder, specs=sel_specs,
                        retrieved_chunks=retrieved_chunks, already_read=already_read,
                    )
                async for ev in gen:
                    yield ev
                llm_calls.extend(holder["llm_calls"])
                meta_actions.extend(holder.get("meta_actions", []))
                stage_text = holder["text"]
                if self.share_history and stage_text:
                    # _agentic mutated stage_msgs (== running_msgs) with the
                    # tool exchanges; record the stage's final answer turn too.
                    running_msgs.append({"role": "assistant", "content": stage_text})
                # Tool-emitted citations (collected by a plugin's tool-result hook).
                for c in holder["citations"]:
                    citations.append(c)
                    emitted_cite_ids.add(getattr(c, "chunk_id", ""))
                    yield stamp(CitationFound(citation=c), trace_id)
                if stage_text:
                    yield stamp(TextDelta(text=stage_text), trace_id)

            if stage_text:
                # Every non-final stage's output carries forward as a labeled note
                # so later stages compose on what earlier ones produced (the final
                # stage's text is the answer, so it never needs to be a note).
                if not is_last_stage:
                    notes.append(f"[{stage.id}] {stage_text}")
                answer = stage_text or answer

            stage_calls = llm_calls[calls_before:]
            stage_in_tok = sum((c.get("usage") or {}).get("input_tokens", 0) for c in stage_calls)
            flow_stages_trace.append(
                {
                    "flow": flow.id,
                    "stage": stage.id,
                    "loop": stage.loop,
                    "lobes": list(stage.lobes),
                    "steps": steps,
                    # Per-subagent sub-traces (one per fanned-out todo: own prompt + timeline) —
                    # empty unless this stage ran loop="map". The viewer's Subagents panel reads it.
                    "subagents": list((stage_holder or {}).get("subagents", [])),
                    "system_prompt": system,  # the composed system text (Prompt panel)
                    "system_segments": system_segments,  # provenance: colour by lobe/section
                    # Context telemetry (Phase 1): per-hop funnel tail + per-stage
                    # input tokens + the attention tier assignments. Makes the
                    # context cost attributable instead of an opaque turn total.
                    "metadata": {
                        "hops": len(stage_calls),
                        "input_tokens": stage_in_tok,
                        "funnel_obs_chars": list(
                            (stage_holder or {}).get("funnel_obs_chars", [])
                        ),
                        "tool_selection": tool_sel,
                        "skill_selection": skill_sel,
                    },
                    "attention": attn_trace,
                }
            )
            yield stamp(StageEnd(flow=flow.id, stage=stage.id), trace_id)

            # ── Navigator hook (post-stage): are we good to go? what runs next? ──
            # The pure ``_next_phase`` reads a model directive (scratchpad["nav_request"])
            # or the deterministic DoD check, and returns the next cursor index (advance /
            # redo / goto / done) — apply-gated + budgeted, pinned cite/filter always run.
            total_runs += 1
            runs[stage.id] = runs.get(stage.id, 0) + 1
            nxt, rewind_to, nav_action, nav_reason = self._next_phase(
                i, stages, turn_ctx, stage, stage_text, runs, redo_budget=REDO_BUDGET,
            )
            if nav_action != "advance":
                meta_actions.append(
                    {"action": nav_action, "reason": nav_reason, "stage": stage.id}
                )
                yield stamp(MetaAction(action=nav_action, reason=nav_reason), trace_id)
            if rewind_to is not None and rewind_to in phase_ckpt:
                m, nt, ct = phase_ckpt[rewind_to]
                del running_msgs[m:]
                del notes[nt:]
                del citations[ct:]
            i = nxt

        # Persist activated skills onto the session so a loaded SOP keeps driving
        # across turns (the ActivateSkill tool appended to this list mid-turn).
        if self._skill_runtime is not None and hasattr(state, "skills_in_use"):
            state.skills_in_use = list(turn_ctx.lobe_outputs.get("skills_in_use", []) or [])

        # Persist a metacognition flow bias (the meta_control tool recorded it this turn).
        # Flow is resolved once at turn start, so the bias takes effect NEXT turn: a path
        # recognizer reads it via build_context (a deterministic signal, not an LLM judge).
        if hasattr(state, "meta_flow_bias"):
            _bias = turn_ctx.lobe_outputs.get("meta_flow_bias")
            if isinstance(_bias, str) and _bias:
                state.meta_flow_bias = _bias

        # Citation extraction/backfill + ground-or-refuse run in the finalize hook
        # (RagPlugin), not here — the engine core carries no citation logic.
        result = await self._finalize(
            trace_id,
            answer,
            citations,
            path,
            resolution,
            flow_stages_trace,
            meta_actions,
            flow,
            usage_before,
            llm_calls,
            degraded,
            scratchpad=(turn_ctx.scratchpad.snapshot() if getattr(turn_ctx, "scratchpad", None) else None),
            retrieved_chunks=retrieved_chunks,
        )
        # Stream CitationFound for any citation a finalize hook added (one-shot text
        # extraction + backfill) that wasn't already emitted during the loop.
        for c in result.citations:
            cid = getattr(c, "chunk_id", "")
            if cid not in emitted_cite_ids:
                emitted_cite_ids.add(cid)
                yield stamp(CitationFound(citation=c), trace_id)
        yield stamp(Final(result=result), trace_id)

    def _select_flow(self, path: dict) -> Flow:
        name = path.get("name")
        if name and name in self.flow_by_id:
            return self.flow_by_id[name]
        # Emergent / unknown → the named fallback flow, else qna, else first.
        for fb in ("fallback", "qna"):
            if fb in self.flow_by_id:
                return self.flow_by_id[fb]
        return self.flows[0] if self.flows else Flow("qna", steps=())

    @staticmethod
    def _scoped_stage(stage: Stage, *, lobes: list[str]) -> Stage:
        """A copy of ``stage`` with a narrowed lobe slice (metacognition trim). Preserves
        loop/tools/overrides so only the consulted lobes change for this step."""
        return Stage(
            stage.id, lobes=lobes, loop=stage.loop, tools=list(stage.tools),
            fanout_key=stage.fanout_key, fanout_parallel=stage.fanout_parallel,
            fanout_max=stage.fanout_max, fanout_isolated=stage.fanout_isolated,
            threshold=stage.threshold,
            model=stage.model, temperature=stage.temperature,
            max_tokens=stage.max_tokens, hops=stage.hops, system_prompt=stage.system_prompt,
        )

    @staticmethod
    def _fanout_with_structure(stage: Stage, sp: Any) -> Stage:
        """Apply a supervisor's per-turn structure choice to a ``loop="map"`` stage.

        The supervisor (a planning ``supervise`` step) writes ``scratchpad["plan_structure"]``
        — ``"fanout"`` (independent steps → parallel + isolated subagent per item) or
        ``"sequential"`` (dependent steps → state-carry, in order); a dict
        ``{parallel, isolated}`` is also honored. Returns a stage scoped to the chosen
        flags, or ``stage`` unchanged when no choice was written (default-network parity)."""
        struct = sp.get("plan_structure") if sp is not None else None
        if not struct:
            return stage
        parallel, isolated = stage.fanout_parallel, stage.fanout_isolated
        if isinstance(struct, str):
            s = struct.lower()
            if s in ("fanout", "parallel"):
                parallel, isolated = True, True
            elif s in ("sequential", "serial"):
                parallel = False
        elif isinstance(struct, dict):
            parallel = bool(struct.get("parallel", parallel))
            isolated = bool(struct.get("isolated", isolated))
        if parallel == stage.fanout_parallel and isolated == stage.fanout_isolated:
            return stage
        return Stage(
            stage.id, lobes=list(stage.lobes), loop="map", tools=list(stage.tools),
            fanout_key=stage.fanout_key, fanout_parallel=parallel,
            fanout_max=stage.fanout_max, fanout_isolated=isolated,
            threshold=stage.threshold, model=stage.model, temperature=stage.temperature,
            max_tokens=stage.max_tokens, hops=stage.hops, system_prompt=stage.system_prompt,
        )

    @staticmethod
    def _stage_carries_pinned(stage: Stage) -> bool:
        """A step consulting a pinned lobe (cite/filter) is never skippable — the
        ground-or-refuse contract is not a meta decision (citations-mandatory)."""
        return bool(set(stage.lobes) & PINNED_UNSKIPPABLE)

    def _dod_check(
        self, stage: Stage, stage_text: str, runs: dict[str, int], *, redo_budget: int
    ) -> tuple[str, str | None, str] | None:
        """Deterministic "are we good to go?" — the free half of the Navigator.

        Returns a ``(action, target, reason)`` nav directive, or ``None`` (good_to_go →
        advance). Today it redoes a phase that clearly missed its definition of done —
        an active phase (single/agentic/map) that produced no output at all — bounded by
        the redo budget. Pinned (cite/filter) phases and pure-prompt (``none``) phases are
        never redone. Richer DoD criteria (the ``stage.dod`` tokens) are judged by the
        model Navigator, which sees the brief. Pure function of (stage, output, runs)."""
        if self._stage_carries_pinned(stage) or stage.loop == "none":
            return None
        if runs.get(stage.id, 0) > redo_budget:
            return None  # out of redo budget — move on
        if not str(stage_text or "").strip():
            return ("redo_phase", stage.id, "phase produced no output (definition of done unmet)")
        return None

    def _next_phase(
        self, i: int, stages: list[Stage], turn_ctx: Any, stage: Stage, stage_text: str,
        runs: dict[str, int], *, redo_budget: int,
    ) -> tuple[int, str | None, str, str]:
        """The Navigator enactor — pure function of (cursor, written directive, budgets).

        Resolves the next cursor index from a model directive (``scratchpad["nav_request"]``,
        one-shot) else the deterministic DoD check, then enacts it: ``advance`` (i+1),
        ``redo_phase`` (re-run, rewind), ``goto_phase`` (jump, rewind if backward), or ``done``.
        Apply-gated (off ⇒ advance) and budgeted; a jump/finish that would leave an un-run
        pinned phase (cite/filter) ahead is redirected to that phase so grounding always runs.
        Returns ``(next_index, rewind_to_id|None, action, reason)``."""
        n = len(stages)
        by_id = {s.id: k for k, s in enumerate(stages)}
        sp = getattr(turn_ctx, "scratchpad", None)
        action, target, reason = "advance", None, ""

        req = sp.get("nav_request") if sp is not None else None
        if isinstance(req, dict) and req.get("to"):
            sp.set("nav_request", None)  # one-shot
            to = str(req["to"]).lower()
            reason = str(req.get("reason") or "meta_control navigate")
            if to == "redo":
                action, target = "redo_phase", stage.id
            elif to == "done":
                action = "done"
            elif to in ("next", "advance"):
                action = "advance"
            elif to in by_id:
                action, target = "goto_phase", to
        else:
            nav = self._dod_check(stage, stage_text, runs, redo_budget=redo_budget)
            if nav is not None:
                action, target, reason = nav

        # Apply-gate the phase moves — off by default ⇒ the cursor advances (parity).
        if action in ("redo_phase", "goto_phase") and not self.metacognition.should_apply(action):
            action, target, reason = "advance", None, ""

        rewind: str | None = None
        if action == "redo_phase" and target in by_id and runs.get(target, 0) <= redo_budget:
            nxt, rewind = by_id[target], target
        elif action == "goto_phase" and target in by_id:
            nxt = by_id[target]
            rewind = target if nxt <= i else None
        elif action == "done":
            nxt = n
        else:  # advance (or a non-enactable directive)
            nxt, action = i + 1, "advance"

        # Pin safety: never exit with an un-run pinned phase still ahead.
        if nxt >= n:
            for k, s in enumerate(stages):
                if self._stage_carries_pinned(s) and runs.get(s.id, 0) == 0:
                    return k, None, "advance", "run pinned grounding step before finishing"
        return nxt, rewind, action, reason

    def _model_label(self, stage: Stage) -> str:
        return stage.model or getattr(self.client, "model", "") or ""

    def _embed_batch(self) -> Any:
        """``embed(texts) -> vectors`` for L2 attention, or None (⇒ L1-only)."""
        return self.embed

    def _embed_one_cb(self) -> Any:
        """``embed_one(text) -> vector`` (over the batch embedder), or None."""
        emb = self.embed
        if emb is None:
            return None

        def _one(text: str) -> Any:
            try:
                vecs = emb([text])
                return vecs[0] if vecs is not None and len(vecs) else None
            except Exception:
                return None

        return _one

    async def _prefetch(self, query: str, state: SessionState, path: str | None = None) -> dict:
        """Run plugin prefetch hooks → a snapshot merged into the TurnContext.

        The resolved intent ``path`` is offered to each hook (so e.g. a KB-grounding
        hook can skip retrieval on a ``relational`` greeting turn). Hooks that don't
        accept it keep the legacy 2-arg signature — tried back-compatibly."""
        snapshot: dict = {}
        for hook in self._prefetch_hooks:
            try:
                try:
                    data = hook(query, state, path)
                except TypeError:
                    data = hook(query, state)
                if hasattr(data, "__await__"):
                    data = await data
                if isinstance(data, dict):
                    snapshot.update(data)
            except Exception:
                continue
        return snapshot

    def _run_tool_filters(self, stage_id: str, name: str, inp: dict) -> str | None:
        """Run the registered tool-call filters; the first to return a string
        short-circuits the call with that string as the tool result. A filter
        that raises is ignored (never breaks the turn)."""
        for filt in self._tool_filters:
            try:
                r = filt(stage_id, name, inp)
            except Exception:
                r = None
            if isinstance(r, str):
                return r
        return None

    async def _call(
        self, stage: Stage, system: str, messages: list[dict], tools: list[dict] | None = None,
        *, max_tokens: int | None = None,
    ) -> Any:
        return await self.client(
            stage=stage.id,
            system=system,
            messages=messages,
            max_tokens=max_tokens or stage.max_tokens or self.default_max_tokens,
            temperature=stage.temperature if stage.temperature is not None else 0.0,
            tools=tools or None,
        )

    async def _agentic(
        self,
        stage: Stage,
        system: str,
        msgs: list[dict],
        trace_id: str,
        steps: list[dict],
        holder: dict,
        specs: list[dict] | None = None,
        *,
        retrieved_chunks: list[dict] | None = None,
        already_read: set[str] | None = None,
    ) -> AsyncIterator[Any]:
        specs = self._tool_specs(stage) if specs is None else specs
        # The turn's evidence channel (shared by the caller across stages/hops).
        # Default to fresh per-call containers so a direct ``_agentic`` call (tests)
        # still works; the engine passes the turn's pool so evidence accumulates.
        if retrieved_chunks is None:
            retrieved_chunks = []
        if already_read is None:
            already_read = set()
        max_hops = stage.hops or self.max_hops
        # No-progress / repetition break (opt-in). When set, a run of `patience`
        # consecutive hops whose every tool call repeats an already-seen
        # (name, args) fingerprint is treated as a stall: the model is steered to
        # converge and the next hop is forced tool-free so it must answer.
        # Unset ⇒ no-op, byte-identical to the unbounded loop.
        patience = int(self.budgets.get("stall_patience", 0)) if self.budgets else 0
        # Structural allowlist enforcement (opt-in): when a stage declares `tools`,
        # the runtime refuses to *execute* a tool outside that set (essentials —
        # memory/recall/note — always pass). The per-stage spec list only hides a
        # tool from the prompt; without this, a model can still call an unlisted
        # tool from its training priors and the runtime would run it (the live
        # "read-only survey wrote the doc" bug). Off by default ⇒ unchanged.
        enforce = bool(self.budgets.get("enforce_tool_allowlist")) if self.budgets else False
        allowed: set[str] | None = None
        if enforce and stage.tools:
            allowed = set(stage.tools) | {"memory"} | set(self._MEMORY_ESSENTIALS)
        seen_sigs: set[str] = set()  # (name, output-hash) seen across hops — progress = a NEW one
        stalls = 0
        force_final = False
        # Bound how many times a single hop may be retried after a `max_tokens`
        # truncation, each time with a doubled token budget. A truncated response
        # is NOT a clean end_turn — accepting it silently drops the stage's work
        # (and would accept a half-written deliverable).
        max_trunc_retries = int(self.budgets.get("truncation_retries", 2)) if self.budgets else 2
        trunc_cap = int(self.budgets.get("truncation_token_cap", 16000)) if self.budgets else 16000
        for hop in range(max_hops):
            loop_tools = [] if (hop >= max_hops - 1 or force_final) else specs
            sent_messages = list(msgs)  # snapshot the real bytes sent this hop
            mt = stage.max_tokens or self.default_max_tokens
            trunc = 0
            while True:
                resp = await self._call(stage, system, msgs, loop_tools, max_tokens=mt)
                if (
                    getattr(resp, "stop_reason", "") == "max_tokens"
                    and trunc < max_trunc_retries
                    and mt < trunc_cap
                ):
                    trunc += 1
                    mt = min(mt * 2, trunc_cap)
                    holder.setdefault("meta_actions", []).append(
                        {"action": "truncation_retry", "stage": stage.id, "hop": hop, "max_tokens": mt}
                    )
                    continue
                break
            think = _text_of(resp)
            call = {
                "stage": stage.id, "hop": hop,
                "stop_reason": getattr(resp, "stop_reason", "end_turn"),
                "usage": _call_usage(resp), "response": _response_blocks(resp),
                "tool_results": [], "system": system, "messages": sent_messages,
                "model": self._model_label(stage),
                "temperature": stage.temperature if stage.temperature is not None else 0.0,
                "max_tokens": mt,
                "tool_count": len(loop_tools), "tools": _tool_defs(loop_tools),
            }
            holder["llm_calls"].append(call)
            if getattr(resp, "stop_reason", "end_turn") == "tool_use":
                if think:  # interim reasoning before tool calls
                    steps.append({"kind": "thinking", "text": think})
                msgs.append({"role": "assistant", "content": _assistant_content(resp)})
                results: list[dict] = []
                for tu in _tool_uses(resp):
                    name, inp, tid = tu.name, (tu.input or {}), getattr(tu, "id", "")
                    steps.append({"kind": "tool_use", "name": name, "input": inp})
                    yield stamp(ToolCall(id=tid, name=name, input=inp), trace_id)
                    # Structural allowlist enforcement (opt-in): refuse a tool the
                    # active stage never declared — before any filter or execution.
                    if allowed is not None and name not in allowed:
                        out = (
                            f"Error: '{name}' is not available in the '{stage.id}' step. "
                            f"Available tools: {', '.join(sorted(allowed))}. Use one of those."
                        )
                    else:
                        # Tool-call filters (Phase 5): a guard may short-circuit a call
                        # (e.g. a redundant heavy rewrite, or a write in a read-only
                        # stage) by returning a substitute result string.
                        out = self._run_tool_filters(stage.id, name, inp)
                        if out is None:
                            out = (
                                await self.tools.call_tool(
                                    name, inp, retrieved_chunks, already_read
                                )
                                if self.tools
                                else "(no tools)"
                            )
                    steps.append({"kind": "tool_result", "name": name, "output": out})
                    yield stamp(ToolResult(id=tid, name=name, output=out), trace_id)
                    holder["tool_calls"].append({"name": name, "input": inp, "output": out})
                    # Citations a tool emits ({"citations": [...]}) are pulled by a
                    # plugin's tool-result hook (RagPlugin) — not engine core logic.
                    for _trh in self._tool_result_hooks:
                        _cits = _trh(name, out)
                        if _cits:
                            holder["citations"].extend(_cits)
                    results.append({"type": "tool_result", "tool_use_id": tid, "content": out})
                    call["tool_results"].append({"tool_use_id": tid, "name": name, "output": out})
                msgs.append({"role": "user", "content": results})
                # PreAct: spent observations shrink to one-line hints so the
                # prompt funnels toward the answer instead of growing O(hops) —
                # what makes hundreds of tool calls fit a bounded window.
                if self.funnel:
                    from agent_sdk.react.funnel import (
                        compact_observations,
                        obs_tail_tokens,
                        score_observations,
                        tier_observations,
                    )

                    # Universal memory: offload spent bodies into the store and fold them into a
                    # dense digest that names the re-fetchable handle (the model reads it back via
                    # the `recall` tool). None ⇒ the default deterministic hint (byte-identical).
                    summ = self.memory_store.compaction_summarizer() if self.memory_store else None
                    ws_budget = self.budgets.get("working_set_budget")
                    if ws_budget:
                        # Value-aware, budget-driven discipline (Phase 2, opt-in):
                        # pin the highest-CDS observations by the current goal so
                        # value — not just recency — survives the funnel, and
                        # compact only when the tail actually exceeds the budget.
                        goal = f"{stage.description or stage.id}. {think}".strip()
                        # Pin the top-`keep_top` observations by CDS vs the goal.
                        # The goal is stable within a stage, so the pinned set is
                        # naturally stable hop-to-hop (low cache thrash) AND bounded
                        # — unlike a monotone union, which would pin everything full.
                        keep = score_observations(
                            msgs, goal=goal, embed_one=self._embed_one_cb(),
                            weights=self.weights or None,
                            keep_top=int(self.budgets.get("working_set_keep", 4)),
                        )
                        holder["keep_ids"] = keep
                        msgs[:] = tier_observations(
                            msgs, hop=hop, keep_last_full=2,
                            keep_full_ids=keep, keep_errors_full=True, summarize=summ,
                        )
                        if obs_tail_tokens(msgs) > int(ws_budget):
                            msgs[:] = compact_observations(
                                msgs, keep_last_full=2, keep_full_ids=keep,
                                keep_errors_full=True, summarize=summ,
                                max_spent=int(self.budgets.get("working_set_max_spent", 6)),
                            )
                    else:
                        # Default — byte-identical to pre-Phase-2: recency-only
                        # tiering + fixed hop%24 compaction.
                        msgs[:] = tier_observations(msgs, hop=hop, keep_last_full=2, summarize=summ)
                        if hop and hop % 24 == 0:
                            msgs[:] = compact_observations(msgs, keep_last_full=2, summarize=summ)
                # Telemetry (Phase 1): record the observation-tail size after this
                # hop's re-tiering so the funnel's growth (or plateau) is visible.
                if "funnel_obs_chars" in holder:
                    holder["funnel_obs_chars"].append(_obs_tail_chars(msgs))
                # Stall detection (semantic): a hop makes *progress* only if some
                # tool produced a NON-error result the agent hasn't seen before —
                # a new file, a new search hit, a fresh memory read. Repeated reads,
                # errors, and refused writes all return a seen/error output and
                # count as no progress. Patience runs of no progress ⇒ steer to
                # converge and force a tool-free final hop.
                if patience and call["tool_results"]:
                    progressed = False
                    for tr in call["tool_results"]:
                        out_s = str(tr.get("output", ""))
                        is_error = out_s.startswith(("Error", "Refused", "(no "))
                        sig = f"{tr.get('name')}:{hash(out_s)}"
                        if not is_error and sig not in seen_sigs:
                            progressed = True
                        seen_sigs.add(sig)
                    stalls = 0 if progressed else stalls + 1
                    if stalls >= patience and not force_final:
                        force_final = True
                        holder.setdefault("meta_actions", []).append(
                            {"action": "stall_break", "stage": stage.id, "hop": hop}
                        )
                        steer = (
                            "You are repeating tool calls without making progress. "
                            "Stop exploring and produce your final answer now using "
                            "what you already have."
                        )
                        # Merge into the trailing user turn to keep roles alternating
                        # (the next hop is forced tool-free, so the model must answer).
                        if msgs and msgs[-1].get("role") == "user":
                            content = msgs[-1]["content"]
                            if isinstance(content, list):
                                content.append({"type": "text", "text": steer})
                            else:
                                msgs[-1]["content"] = f"{content}\n\n{steer}"
                        else:
                            msgs.append({"role": "user", "content": steer})
            else:
                # A `max_tokens` stop that survived the retry loop is a genuine
                # truncation (budget cap hit) — flag it so a half-finished answer
                # isn't mistaken for a deliberate, complete end_turn.
                if getattr(resp, "stop_reason", "") == "max_tokens":
                    holder.setdefault("meta_actions", []).append(
                        {"action": "truncated_final", "stage": stage.id, "hop": hop}
                    )
                holder["text"] = think
                steps.append({"kind": "answer", "text": think})
                return
        # Final hop without an end_turn — use whatever text we have.
        holder["text"] = holder["text"] or think
        if not holder["text"]:
            # The loop hit the hop cap without ever producing prose — e.g. the model
            # kept emitting tool calls (incl. recovered markup) to the very last,
            # tool-free hop. Force ONE tool-free answer hop so the turn always surfaces
            # a reply (a grounded refusal, here) instead of ending silent.
            final_mt = stage.max_tokens or self.default_max_tokens
            resp = await self._call(stage, system, msgs, [], max_tokens=final_mt)
            holder["text"] = _text_of(resp)
            steps.append({"kind": "answer", "text": holder["text"]})
            holder["llm_calls"].append({
                "stage": stage.id, "hop": max_hops,
                "stop_reason": getattr(resp, "stop_reason", "end_turn"),
                "usage": _call_usage(resp), "response": _response_blocks(resp),
                "tool_results": [], "system": system, "messages": list(msgs),
                "model": self._model_label(stage),
                "temperature": stage.temperature if stage.temperature is not None else 0.0,
                "max_tokens": final_mt, "tool_count": 0, "tools": [],
            })

    @staticmethod
    def _new_th() -> dict[str, Any]:
        return {"text": "", "tool_calls": [], "citations": [], "llm_calls": [],
                "funnel_obs_chars": []}

    @staticmethod
    def _merge_th(holder: dict, th: dict) -> None:
        """Roll one worker's metrics up into the parent holder (order-stable)."""
        holder["llm_calls"].extend(th.get("llm_calls", []))
        holder["tool_calls"].extend(th.get("tool_calls", []))
        holder["citations"].extend(th.get("citations", []))
        holder.setdefault("meta_actions", []).extend(th.get("meta_actions", []))
        holder["funnel_obs_chars"].extend(th.get("funnel_obs_chars", []))

    @staticmethod
    def _th_out_tokens(th: dict) -> int:
        """Best-effort output-token count for one worker (the memo's cost)."""
        total = 0
        for c in th.get("llm_calls", []):
            u = c.get("usage") or {}
            with contextlib.suppress(TypeError, ValueError):
                total += int(u.get("output_tokens") or 0)
        return total

    def _compose_map_item(
        self, stage: Stage, item: dict, label: str, msgs: list[dict], *,
        ctx: dict, state: SessionState, carried: list[str], turn_ctx: Any, sel: list[dict],
    ) -> tuple[Stage, str, list[dict], list[dict]]:
        """Build the scoped Stage + system + messages + tool-specs for one work-item.

        Shared by the sequential and parallel paths so a subagent's spec (its own
        ``system_prompt`` / ``tools`` / ``lobes`` / ``model`` / ``max_tokens`` / ``hops``)
        is applied identically. ``carried`` is the prior-results note slice (empty in the
        parallel path — independent workers do not carry state)."""
        # Work-item shape is generic: a plain ``{input}`` item OR a plan todo
        # (``{content, prompt, tools, deps}``) — ``prompt``/``content`` are the todo
        # aliases for ``system_prompt``/``input`` so a TodoWrite plan fans out directly.
        scoped = Stage(
            stage.id, lobes=list(item.get("lobes") or stage.lobes), loop="agentic",
            tools=list(item.get("tools") or stage.tools),
            system_prompt=(
                item.get("system_prompt") or item.get("prompt")
                or stage.system_prompt or _MAP_ITEM_PROMPT
            ),
            model=item.get("model") or stage.model,
            max_tokens=item.get("max_tokens") or stage.max_tokens,
            hops=int(item.get("hops") or stage.hops or 12),
        )
        item_specs = sel
        if item.get("tools"):
            keep = set(item["tools"])
            item_specs = [s for s in sel if s.get("name") in keep]
        isys, _seg = self._compose_system_segmented(
            scoped, ctx, state, carried, turn_ctx, tool_specs=item_specs,
        )
        task = item.get("input") or item.get("content") or label
        imsgs = list(msgs) + [{"role": "user", "content": f"Sub-task ({label}): {task}"}]
        return scoped, isys, imsgs, item_specs

    @staticmethod
    def _map_item_pool(
        stage: Stage, retrieved_chunks: list[dict] | None, already_read: set[str] | None,
    ) -> tuple[list[dict] | None, set[str] | None]:
        """The evidence channel a worker runs against.

        Default: the shared turn pool (today's behavior — output boundary already clean via
        the Blackboard raw-chunk rejection). ``fanout_isolated``: a FRESH pool per worker so
        worker A's chunks never enter worker B's window — only its memo returns (doc 12)."""
        if stage.fanout_isolated:
            return [], set()
        return retrieved_chunks, already_read

    async def _map_stage(
        self, stage: Stage, system: str, msgs: list[dict], trace_id: str,
        steps: list[dict], holder: dict, *, ctx: dict, state: SessionState,
        notes: list[str], turn_ctx: Any, sel_specs: list[dict] | None,
        retrieved_chunks: list[dict] | None = None,
        already_read: set[str] | None = None,
    ) -> AsyncIterator[Any]:
        """Generic fan-out (``loop="map"``): run one bounded, SCOPED ``_agentic``
        sub-execution per work-item in ``scratchpad[fanout_key]`` (a list the producing
        stage/plugin filled), each item optionally overriding ``system_prompt`` / ``tools``
        / ``lobes`` / ``model`` / ``max_tokens`` / ``hops``. Two shapes (doc 12), by
        ``stage.fanout_parallel``: **sequential** (default) carries prior results forward as
        notes (state carry — the tasks rail relies on it); **parallel** runs workers
        concurrently (``asyncio.gather``, bounded by ``fanout_max``), independent, no carry.
        Either shape is bounded-failure: a worker that raises/times out is recorded
        ``status="failed"``, never dropped, never sinks the turn. Results →
        ``scratchpad[fanout_key + "_results"]``. Domain-free. Empty list ⇒ degrade to a
        single agentic run (parity)."""
        sp = getattr(turn_ctx, "scratchpad", None)
        items = sp.as_list(stage.fanout_key) if (sp is not None and stage.fanout_key) else []
        # Supervision seam (reason → write → enact): a supervisor step may write
        # ``scratchpad["plan_structure"]`` to choose HOW the plan runs — ``"inline"`` (the main
        # agent works the list itself in THIS stage, no spawn), ``"fanout"`` (parallel + isolated
        # subagent per item), or ``"sequential"`` (subagent per item, state-carry). Pure data; the
        # engine just enacts it. Absent ⇒ the stage's own flags stand (parity).
        structure = sp.get("plan_structure") if sp is not None else None
        if not items or structure == "inline":
            # No work-list, or the supervisor chose inline: the main agent solves every planned
            # piece itself in one agentic loop (the todo_list lobe keeps the plan in view). The
            # plan is still handled — just by the main stage, not by spawned subagents.
            async for ev in self._agentic(
                stage, system, msgs, trace_id, steps, holder, specs=sel_specs,
                retrieved_chunks=retrieved_chunks, already_read=already_read,
            ):
                yield ev
            return

        stage = self._fanout_with_structure(stage, sp)
        cap = max(1, min(40, int(stage.fanout_max or 40)))
        work = [(i, raw if isinstance(raw, dict) else {"input": str(raw)})
                for i, raw in enumerate(items[:cap])]
        sel = sel_specs or []

        subagents: list[dict] = []  # per-worker sub-trace (own prompt + timeline) for the viewer
        if stage.fanout_parallel:
            results = []
            async for ev in self._map_parallel(
                stage, msgs, trace_id, steps, holder, ctx=ctx, state=state, notes=notes,
                turn_ctx=turn_ctx, sel=sel, work=work, results=results, subagents=subagents,
                retrieved_chunks=retrieved_chunks, already_read=already_read,
            ):
                yield ev
        else:
            results = []
            carried = list(notes)
            for i, item in work:
                label = str(item.get("label") or item.get("id") or item.get("content") or f"item{i}")
                scoped, isys, imsgs, item_specs = self._compose_map_item(
                    stage, item, label, msgs, ctx=ctx, state=state, carried=carried,
                    turn_ctx=turn_ctx, sel=sel,
                )
                rc, ar = self._map_item_pool(stage, retrieved_chunks, already_read)
                th = self._new_th()
                wsteps: list[dict] = []
                status, err = "ok", None
                try:
                    async for ev in self._agentic(
                        scoped, isys, imsgs, trace_id, wsteps, th, specs=item_specs,
                        retrieved_chunks=rc, already_read=ar,
                    ):
                        yield ev
                except Exception as exc:  # bounded failure: degrade, never lose the turn
                    status, err = "failed", str(exc)[:200]
                steps.extend(wsteps)
                res = th.get("text", "")
                results.append({"label": label, "result": res, "status": status,
                                "tokens_used": self._th_out_tokens(th), "error": err})
                subagents.append({"label": label, "status": status, "system_prompt": isys,
                                  "steps": wsteps, "llm_calls": list(th.get("llm_calls", [])),
                                  "tokens_used": self._th_out_tokens(th)})
                carried.append(f"[{label}] {res}")
                self._merge_th(holder, th)

        holder["subagents"] = subagents
        if sp is not None:
            # Store a COMPACT copy: the scratchpad is turn flash-memory (8k/value cap), so a wide
            # fan-out's full result texts would overflow it. Trim each result for the fan-in lobe /
            # telemetry; the FULL text is already in ``holder``/the answer path. Keeps the list a
            # list under the cap so every subagent survives (no zero-results collapse).
            compact = [
                {**r, "result": (str(r.get("result") or "")[:_FANOUT_RESULT_CHARS])}
                for r in results
            ]
            sp.set(stage.fanout_key + "_results", compact)
        holder["text"] = "\n".join(
            f"{r['label']}: {r['result']}".strip() for r in results if r.get("status") != "failed"
        )

    async def _map_parallel(
        self, stage: Stage, msgs: list[dict], trace_id: str, steps: list[dict], holder: dict,
        *, ctx: dict, state: SessionState, notes: list[str], turn_ctx: Any, sel: list[dict],
        work: list[tuple[int, dict]], results: list[dict], subagents: list[dict],
        retrieved_chunks: list[dict] | None, already_read: set[str] | None,
    ) -> AsyncIterator[Any]:
        """Parallel fan-out: workers run concurrently (semaphore-bounded by ``_FANOUT_CONCURRENCY``
        — a conservative cap so a wide fan-out does not burst past the provider's rate/concurrency
        limit), each into its OWN holder / steps / evidence pool; events are buffered and flushed in
        item order (deterministic). The WHOLE worker body (compose + run) is failure-isolated, so a
        worker is always recorded (``status="ok"|"failed"``), never silently dropped. A failed
        worker is retried ONCE serially (recovers transient concurrency rejections); a worker that
        still fails leaves a ``degraded`` marker on the turn. ``results`` is filled in place."""
        cap = max(1, min(len(work), int(stage.fanout_max or 40), _FANOUT_CONCURRENCY))
        sem = asyncio.Semaphore(cap)

        async def worker(idx: int, item: dict) -> dict:
            label = str(item.get("label") or item.get("id") or item.get("content") or f"item{idx}")
            th = self._new_th()
            wsteps: list[dict] = []
            buf: list[Any] = []
            captured: dict[str, Any] = {"isys": ""}
            status, err = "ok", None

            async def run() -> None:
                # Compose INSIDE the guarded body so a failure here is a recorded worker
                # failure (status="failed"), not a BaseException that gather silently drops.
                scoped, isys, imsgs, item_specs = self._compose_map_item(
                    stage, item, label, msgs, ctx=ctx, state=state, carried=list(notes),
                    turn_ctx=turn_ctx, sel=sel,
                )
                captured["isys"] = isys
                rc, ar = self._map_item_pool(stage, retrieved_chunks, already_read)
                async for ev in self._agentic(
                    scoped, isys, imsgs, trace_id, wsteps, th, specs=item_specs,
                    retrieved_chunks=rc, already_read=ar,
                ):
                    buf.append(ev)

            timeout = item.get("timeout")
            async with sem:
                try:
                    if timeout:
                        await asyncio.wait_for(run(), float(timeout))
                    else:
                        await run()
                except Exception as exc:  # bounded failure (TimeoutError included)
                    status, err = "failed", str(exc)[:200] or "timeout"
            return {"label": label, "th": th, "steps": wsteps, "buf": buf,
                    "system_prompt": captured["isys"], "status": status, "err": err,
                    "_item": (idx, item)}

        gathered = await asyncio.gather(
            *(worker(i, item) for i, item in work), return_exceptions=True
        )
        # Normalize: a BaseException escaping gather (shouldn't happen now) becomes a failed stub.
        norm: list[dict] = []
        for (idx, item), res in zip(work, gathered, strict=False):
            if isinstance(res, BaseException):
                label = str(item.get("label") or item.get("id") or item.get("content") or f"item{idx}")
                norm.append({"label": label, "th": self._new_th(), "steps": [], "buf": [],
                             "system_prompt": "", "status": "failed", "err": str(res)[:200],
                             "_item": (idx, item)})
            else:
                norm.append(res)
        # Serial retry of failed workers ONCE — re-run one at a time, which avoids the concurrent
        # burst that usually caused the failure (rate/concurrency limit). Replace on success.
        for k, r in enumerate(norm):
            if r["status"] == "failed":
                idx, item = r["_item"]
                retry = await worker(idx, item)
                if retry["status"] == "ok":
                    norm[k] = retry

        # Flush in submission order — deterministic regardless of completion order.
        failed_n = 0
        for res in norm:
            steps.extend(res["steps"])
            for ev in res["buf"]:
                yield ev
            th = res["th"]
            if res["status"] == "failed":
                failed_n += 1
            results.append({"label": res["label"], "result": th.get("text", ""),
                            "status": res["status"], "tokens_used": self._th_out_tokens(th),
                            "error": res["err"]})
            subagents.append({"label": res["label"], "status": res["status"],
                              "system_prompt": res["system_prompt"], "steps": res["steps"],
                              "llm_calls": list(th.get("llm_calls", [])),
                              "tokens_used": self._th_out_tokens(th)})
            self._merge_th(holder, th)
        if failed_n:
            dg = getattr(turn_ctx, "degraded", None)
            if isinstance(dg, list):
                dg.append(f"fanout:{failed_n}_failed")

    # ── finalize ─────────────────────────────────────────────────────────────
    def _usage_snapshot(self) -> ProviderUsage:
        u = getattr(self.client, "total_usage", None)
        return u if isinstance(u, ProviderUsage) else ProviderUsage()

    def _memory_updates(self) -> list[MemoryUpdate]:
        rt = self.memory_runtime
        updates = getattr(rt, "updates", None) if rt else None
        if not updates:
            return []
        out = [MemoryUpdate(action=u["action"], scope=u["scope"], key=u["key"]) for u in updates]
        updates.clear()
        return out

    async def _finalize(
        self,
        trace_id,
        answer,
        citations,
        path,
        resolution,
        flow_stages_trace,
        meta_actions,
        flow,
        usage_before,
        llm_calls,
        degraded=None,
        scratchpad=None,
        retrieved_chunks=None,
    ) -> AgentResult:
        # Plugin finalize hooks own the grounding/citation contract (extraction,
        # backfill, marker-strip, ground-or-refuse) — the engine carries none of it.
        # Each hook may rewrite the answer, replace citations, or force a refusal.
        refusal_reason: str | None = None
        for hook in self._finalize_hooks:
            res = hook(
                answer, citations, list(retrieved_chunks or []), flow.grounds, self.require_citations
            )
            if inspect.isawaitable(res):
                res = await res
            if res is None:
                continue
            answer, citations, refusal_reason = res
            if refusal_reason:
                break
        after = self._usage_snapshot()
        diff = ProviderUsage(
            input_tokens=after.input_tokens - usage_before.input_tokens,
            output_tokens=after.output_tokens - usage_before.output_tokens,
            cache_read_tokens=after.cache_read_tokens - usage_before.cache_read_tokens,
            cache_write_tokens=after.cache_write_tokens - usage_before.cache_write_tokens,
        )
        usage = Usage.from_provider(diff)
        # Project the per-stage adaptive-exposure records to first-class trace
        # fields (host reads trace.tool_selection / .skill_selection directly).
        tool_selection = [
            {"stage": s.get("stage"), **sel}
            for s in flow_stages_trace
            if (sel := (s.get("metadata") or {}).get("tool_selection"))
        ]
        skill_selection = [
            {"stage": s.get("stage"), "ranking": sel}
            for s in flow_stages_trace
            if (sel := (s.get("metadata") or {}).get("skill_selection"))
        ]
        trace = Trace(
            trace_id=trace_id,
            path=path,
            lobes=resolution.lobes,
            flow_stages=flow_stages_trace,
            blackboard={"activated": resolution.activated, **(scratchpad or {})},
            usage=usage,
            meta_actions=meta_actions,
            llm_calls=llm_calls,
            attention=_attention_rollup(flow_stages_trace),
            tool_selection=tool_selection,
            skill_selection=skill_selection,
            degraded=list(degraded or []),
        )
        # Ground-or-refuse is the RagPlugin's contract: a finalize hook returns a
        # refusal_reason when a grounding turn requires citations but found none.
        # The engine core has no citation gate of its own.
        if refusal_reason:
            msg = self.refusal_message or "No supporting sources were found."
            return AgentResult(
                text=msg,
                status="refused",
                refusal=Refusal(
                    reason=refusal_reason,
                    message=msg,
                ),
                usage=usage,
                trace=trace,
            )
        return AgentResult(
            text=answer,
            status="answered",
            citations=citations,
            usage=usage,
            memory_updates=self._memory_updates(),
            trace=trace,
        )

    def inspect(self, query: str, state: SessionState | None = None) -> Any:
        """The dry, no-LLM routing probe — explain how a turn will route."""
        from agent_sdk.result import ActivationSnapshot

        state = state or SessionState()
        ctx = self.build_context(query, state)
        weights = merge_lobe_weights({}, self.weights)
        resolution = propagate(
            self.lobe_specs, ctx, weights=weights, paths=self.path_specs, min_activation=0.0
        )
        path = resolution.path
        flow = self._select_flow(path)
        stages = [
            s for s in self.stage_registry.resolve(flow.stages) if s.signal(ctx) >= s.threshold
        ]
        lobe_rows = [
            {
                "id": e["id"],
                "layer": e["layer"],
                "activated": e["activated"],
                "score": e["activation"],
                "reason": e["reason"],
            }
            for e in resolution.lobes
        ]
        return ActivationSnapshot(
            path=(path.get("name", "emergent"), path.get("score", 0.0)),
            lobes=lobe_rows,
            flow=[s.id for s in stages],
            budget=dict(self.budgets),
        )

    async def run(self, query: str, state: SessionState | None = None) -> AgentResult:
        result: AgentResult | None = None
        async for ev in self.stream(query, state):
            if isinstance(ev, Final):
                result = ev.result
        assert result is not None
        return result
