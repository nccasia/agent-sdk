"""The generic PreAct turn driver (the engine kernel).

One turn = the deterministic core (recognize flow → activate lobes → resolve
stages → build the per-stage prompt) wrapped around the I/O seams (``LlmCall``,
``ToolRuntime``, ``Memory``). The engine streams typed events throughout and
assembles a ``FinalEnvelope``-shaped :class:`AgentResult` + a full :class:`Trace`.

It is a pure function of ``(network, context)`` for everything except the model
and tool calls — exactly the split that makes the core portable (docs/porting.md).
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import re
import unicodedata
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
from agent_sdk.lobes.runtime import Lobe, datetime_block
from agent_sdk.metacognition_facade import Metacognition
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

# Generic default prompt for a `loop="map"` sub-task (an item may override it via
# `system_prompt`). Domain-free: the engine knows "sub-tasks", not "todos".
_MAP_ITEM_PROMPT = (
    "Complete ONLY this sub-task using the available tools, then state its result "
    "concisely. Prior sub-task results are in the notes — build on them; do not redo "
    "them or start others."
)

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


def _citations_from_text(text: str, chunks: list[dict]) -> list[Citation]:
    """Citations for a one-shot (single-loop) answer: each evidence chunk whose
    ``[chunk_id]`` literally appears in the answer becomes a Citation. Lets a
    one-shot RAG stage ground from a prefetch-seeded evidence channel, the way the
    agentic path grounds from tool-output citations. No tool loop required."""
    if not text or not chunks:
        return []
    out: list[Citation] = []
    seen: set[str] = set()
    for ch in chunks:
        cid = str(ch.get("chunk_id") or "")
        if cid and cid not in seen and f"[{cid}]" in text:
            seen.add(cid)
            out.append(Citation(
                chunk_id=cid,
                source_ref=str(ch.get("source_ref") or ""),
                supporting_span=(0, len(text)),
            ))
    return out


_BACKFILL_MIN_ANSWER_CHARS = 60   # a refusal/one-liner is shorter ⇒ never backfilled
_BACKFILL_MAX_ADD = 3             # cap added citations
_BACKFILL_MIN_OVERLAP = 4         # ≥ this many distinctive chunk tokens in the answer


def _content_tokens(text: str) -> set[str]:
    """Distinctive content tokens (NFC-lower, len≥4, deduped) for overlap scoring.
    Generic — no language-specific stopword list; the length filter drops most
    function words while keeping content syllables/words."""
    norm = unicodedata.normalize("NFC", text or "").lower()
    return {t for t in re.split(r"[^0-9a-zà-ỹ_]+", norm) if len(t) >= 4}


def _backfill_citations(
    answer: str, chunks: list[dict], existing: list[Citation]
) -> list[Citation]:
    """Cite the retrieved chunks an answer actually USED but didn't `[chunk_id]`-mark.

    A grounded answer that paraphrases (the model omitted the marker) still needs
    its source cited for grounding/scoring. For each not-yet-cited evidence chunk
    (top score first), attach a Citation when enough of the chunk's distinctive
    content tokens appear in the answer — so a refusal/one-liner (too short) or a
    chitchat answer (no KB-content overlap) gets ZERO backfill. Capped. Domain-free.
    """
    if not answer or len(answer) < _BACKFILL_MIN_ANSWER_CHARS or not chunks:
        return []
    cited = {c.chunk_id for c in existing}
    ans_tokens = _content_tokens(answer)
    if not ans_tokens:
        return []
    ranked = sorted(chunks, key=lambda c: float(c.get("score") or 0), reverse=True)
    out: list[Citation] = []
    for ch in ranked:
        cid = str(ch.get("chunk_id") or "")
        if not cid or cid in cited:
            continue
        ctoks = _content_tokens(ch.get("text") or "")
        if not ctoks:
            continue
        shared = len(ctoks & ans_tokens)
        # absolute overlap, or (for short chunks) a strong relative overlap
        if shared >= _BACKFILL_MIN_OVERLAP or (shared and shared >= 0.5 * len(ctoks)):
            cited.add(cid)
            out.append(Citation(
                chunk_id=cid,
                source_ref=str(ch.get("source_ref") or ""),
                supporting_span=(0, len(answer)),
            ))
            if len(out) >= _BACKFILL_MAX_ADD:
                break
    return out


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
        self.require_citations = require_citations
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
        return {
            "query": query,
            "is_question": q.endswith("?") or low.startswith(_WH),
            "word_count": len(q.split()),
            "has_history": bool(state.history),
            "ambiguous": False,
        }

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
        snapshot = await self._prefetch(query, state)
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
        flow_stages_trace: list[dict] = []
        meta_actions: list[dict] = []
        llm_calls: list[dict] = []
        answer = ""

        for stage_index, stage in enumerate(stages):
            is_last_stage = stage_index == len(stages) - 1
            yield stamp(StageStart(flow=flow.id, stage=stage.id), trace_id)
            if self.share_history and stage_index > 0:
                # Keep user/assistant alternation across stage boundaries.
                running_msgs.append(
                    {"role": "user", "content": f"Next step ({stage.id}): "
                     f"{stage.description or stage.id}"}
                )
            decision = self.metacognition.plan_next(target_flow=flow.id, target_step=stage.id)
            if decision.action != "continue":
                meta_actions.append(
                    {"action": decision.action, "reason": decision.reason, "stage": stage.id}
                )
                yield stamp(MetaAction(action=decision.action, reason=decision.reason), trace_id)
                if decision.action == "skip_step" and self.metacognition.should_apply("skip_step"):
                    flow_stages_trace.append(
                        {"flow": flow.id, "stage": stage.id, "skipped": True, "steps": []}
                    )
                    yield stamp(StageEnd(flow=flow.id, stage=stage.id), trace_id)
                    continue

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
                steps.append({"kind": "answer", "text": stage_text})
                # One-shot grounding: a single-loop answer carries no tool-driven
                # citations, so resolve [chunk_id] mentions against the (prefetch-
                # seeded) evidence channel — lets a one-shot RAG stage ground like
                # the agentic path. No-op when the answer cites nothing.
                for _c in _citations_from_text(stage_text, retrieved_chunks):
                    citations.append(_c)
                    yield stamp(CitationFound(citation=_c), trace_id)
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
                for c in holder["citations"]:
                    citations.append(c)
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

        # Persist activated skills onto the session so a loaded SOP keeps driving
        # across turns (the ActivateSkill tool appended to this list mid-turn).
        if self._skill_runtime is not None and hasattr(state, "skills_in_use"):
            state.skills_in_use = list(turn_ctx.lobe_outputs.get("skills_in_use", []) or [])

        # Citation backfill: a grounded answer that drew on the retrieved chunks
        # but didn't emit [chunk_id] markers (the model paraphrased) still needs
        # its source cited. Overlap-gated + capped, so refusals/chitchat get none.
        for _bc in _backfill_citations(answer, retrieved_chunks, citations):
            citations.append(_bc)
            yield stamp(CitationFound(citation=_bc), trace_id)

        result = self._finalize(
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
        )
        yield stamp(Final(result=result), trace_id)

    def _select_flow(self, path: dict) -> Flow:
        name = path.get("name")
        if name and name in self.flow_by_id:
            return self.flow_by_id[name]
        # Emergent / unknown → the named fallback flow, else qna, else first.
        for fb in ("fallback", "qna"):
            if fb in self.flow_by_id:
                return self.flow_by_id[fb]
        return self.flows[0] if self.flows else Flow("qna", stages=["synthesize"])

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

    async def _prefetch(self, query: str, state: SessionState) -> dict:
        """Run plugin prefetch hooks → a snapshot merged into the TurnContext."""
        snapshot: dict = {}
        for hook in self._prefetch_hooks:
            try:
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
                    self._extract_citations(out, holder["citations"])
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
        / ``lobes`` / ``model`` / ``max_tokens`` / ``hops``; prior results carry forward as
        notes (state carry). Results are written back to ``scratchpad[fanout_key + "_results"]``.
        Domain-free — the engine knows "sub-tasks", not what they mean. Empty list ⇒ degrade
        to a single agentic run (parity)."""
        sp = getattr(turn_ctx, "scratchpad", None)
        items = sp.as_list(stage.fanout_key) if (sp is not None and stage.fanout_key) else []
        if not items:
            async for ev in self._agentic(
                stage, system, msgs, trace_id, steps, holder, specs=sel_specs,
                retrieved_chunks=retrieved_chunks, already_read=already_read,
            ):
                yield ev
            return

        carried = list(notes)
        results: list[dict] = []
        sel = sel_specs or []
        for i, raw in enumerate(items[:40]):
            item = raw if isinstance(raw, dict) else {"input": str(raw)}
            label = str(item.get("label") or item.get("id") or f"item{i}")
            scoped = Stage(
                stage.id, lobes=list(item.get("lobes") or stage.lobes), loop="agentic",
                tools=list(item.get("tools") or stage.tools),
                system_prompt=item.get("system_prompt") or stage.system_prompt or _MAP_ITEM_PROMPT,
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
            imsgs = list(msgs) + [{"role": "user",
                                   "content": f"Sub-task ({label}): {item.get('input') or label}"}]
            th: dict[str, Any] = {"text": "", "tool_calls": [], "citations": [],
                                  "llm_calls": [], "funnel_obs_chars": []}
            async for ev in self._agentic(
                scoped, isys, imsgs, trace_id, steps, th, specs=item_specs,
                retrieved_chunks=retrieved_chunks, already_read=already_read,
            ):
                yield ev
            res = th.get("text", "")
            results.append({"label": label, "result": res})
            carried.append(f"[{label}] {res}")
            holder["llm_calls"].extend(th["llm_calls"])
            holder["tool_calls"].extend(th.get("tool_calls", []))
            holder["citations"].extend(th["citations"])
            holder.setdefault("meta_actions", []).extend(th.get("meta_actions", []))
            holder["funnel_obs_chars"].extend(th.get("funnel_obs_chars", []))
        if sp is not None:
            sp.set(stage.fanout_key + "_results", results)
        holder["text"] = "\n".join(f"{r['label']}: {r['result']}".strip() for r in results)

    @staticmethod
    def _extract_citations(tool_output: str, out: list[Citation]) -> None:
        """A KB-style tool may surface citations as JSON ``{"citations": [...]}``."""
        try:
            data = json.loads(tool_output)
        except (json.JSONDecodeError, TypeError):
            return
        if isinstance(data, dict):
            for c in data.get("citations", []) or []:
                try:
                    out.append(Citation(**c) if isinstance(c, dict) else c)
                except Exception:
                    continue

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

    def _finalize(
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
    ) -> AgentResult:
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
            blackboard={"activated": resolution.activated},
            usage=usage,
            meta_actions=meta_actions,
            llm_calls=llm_calls,
            attention=_attention_rollup(flow_stages_trace),
            tool_selection=tool_selection,
            skill_selection=skill_selection,
            degraded=list(degraded or []),
        )
        # Ground-or-refuse: a grounding flow with citations required but none found.
        if self.require_citations and flow.grounds and not citations:
            return AgentResult(
                text="I cannot confirm that from the available sources.",
                status="refused",
                refusal=Refusal(reason="no_citations", message="No supporting sources were found."),
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
