"""``PreactAgent`` — the public façade over the PreAct engine.

Wires the building blocks (lobes / stages / flows / skills / tools), the I/O seams
(client, session, memory), and the extensions (plugins, metacognition) into a
ready-to-run agent. Building blocks default to the built-in PreAct network when
omitted; persistence defaults to in-memory.

    agent = PreactAgent(client=AnthropicClient("claude-opus-4-6"),
                        instructions="You are a helpful research assistant.",
                        tools=[search])
    result = await agent.query("What changed in v2?")   # one-shot → AgentResult
    async for event in agent.act("What changed in v2?"): ...   # streaming
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Any

from agent_sdk.clients.base import make_client
from agent_sdk.engine import Engine
from agent_sdk.events import AgentStream, Final
from agent_sdk.flow_def import Flow
from agent_sdk.lobes.runtime import Lobe
from agent_sdk.memory import Memory
from agent_sdk.metacognition_facade import Metacognition
from agent_sdk.plugins.base import AgentSetup
from agent_sdk.preact.defaults import Flows, Lobes, Stages
from agent_sdk.result import ActivationSnapshot, AgentResult, Optimization, Trace
from agent_sdk.session import Session, SessionState, Turn
from agent_sdk.stages import Stage

__all__ = ["PreactAgent"]

_DONE = object()


# Injected into the system prompt when universal memory is on (the default) so the agent NATIVELY
# memorizes and recalls — it doesn't depend on the user's instructions mentioning memory.
MEMORY_DIRECTIVE = (
    "You have a persistent memory across this conversation and future ones. A `## Memory` section in "
    "your context lists what you have stored.\n"
    "- MEMORIZE: when you learn durable facts — deadlines, owners, schedules, decisions, values, "
    "preferences, postmortems, agreements — call `note` for EACH distinct fact, one note per fact, "
    "the moment you read it (scope=conversation for things that outlive this turn). Do not skip facts.\n"
    "- RECALL: before answering any question about earlier information, check the `## Memory` list and "
    "use `recall` to pull the detail. Always recall first, then answer.\n"
    "- When a fact changes, note the new value; recall returns the latest."
)


def _resolve_blocks(value: Any, default_factory: Any) -> list:
    """Resolve a building-block arg: ``None``/``"default"`` → built-ins, a registry
    with ``.default()`` → its default, or a plain list passed through."""
    if value is None or value == "default":
        return list(default_factory())
    if hasattr(value, "default") and callable(value.default):
        return list(value.default())
    return list(value)


def _derive_path_specs(flows: list, stages: list) -> list:
    """Compile flows → ``PathSpec`` recognizers (the same derivation the ``Engine``
    runs when ``paths`` is omitted). Used to fold plugin-contributed flows into the
    explicit production path list so their intent is recognized."""
    from agent_sdk.stages import StageRegistry

    reg = StageRegistry(stages)
    specs = []
    for f in flows:
        members: list[str] = []
        for st in reg.resolve(f.stages):
            members.extend(st.lobes)
        members = list(dict.fromkeys(members))
        specs.append(f.to_path_spec(members, bias={m: 1.0 for m in members}))
    return specs


class PreactAgent:
    def __init__(
        self,
        *,
        client: Any,
        instructions: str = "",
        lobes: Any = None,
        stages: Any = None,
        flows: Any = None,
        skills: list[Any] | None = None,
        tools: list[Any] | Any | None = None,
        tool_filters: list[Any] | None = None,
        mcp_servers: list[Any] | None = None,
        session: Session | None = None,
        memory: Memory | None = None,
        plugins: list[Any] | None = None,
        metacognition: Any = "observe",
        weights: dict | None = None,
        budgets: dict | None = None,
        require_citations: bool = False,
        share_history: bool = False,
        tools_in_prompt: bool = False,
        funnel: bool = True,
        universal_memory: bool = True,
        auto_establish: bool = True,
        embed: Any = None,
        tz: str = "UTC",
        lang: str = "en",
        prompt_format: str = "xml",
        context: Any = None,
        host: Any = None,
        pre_turn_gate: Any = None,
    ):
        # Keep the raw config so with_() can produce immutable copies.
        self._config: dict[str, Any] = dict(
            client=client,
            instructions=instructions,
            lobes=lobes,
            stages=stages,
            flows=flows,
            skills=skills,
            tools=tools,
            tool_filters=tool_filters,
            mcp_servers=mcp_servers,
            session=session,
            memory=memory,
            plugins=plugins,
            metacognition=metacognition,
            weights=weights,
            budgets=budgets,
            require_citations=require_citations,
            share_history=share_history,
            tools_in_prompt=tools_in_prompt,
            funnel=funnel,
            universal_memory=universal_memory,
            auto_establish=auto_establish,
            embed=embed,
            tz=tz,
            lang=lang,
            prompt_format=prompt_format,
            context=context,
            host=host,
            pre_turn_gate=pre_turn_gate,
        )

        self.client = make_client(client)
        # Native auto-offload: after each turn, fact-shaped statements in the user's message are
        # established into durable memory (reliable memorize, not model-dependent). On with memory.
        self._auto_establish = bool(universal_memory and auto_establish)
        # Default funnel budget: compact the tool-loop tail on a TOKEN threshold (so context is bounded
        # at ANY hop count) instead of only every 24 hops. Overridable via budgets.
        engine_budgets = dict(budgets or {})
        if funnel and "working_set_budget" not in engine_budgets:
            engine_budgets["working_set_budget"] = (
                2000  # ~8k chars: compaction triggers on a real loop
            )
            engine_budgets.setdefault("working_set_keep", 3)
        self.instructions = instructions
        self.session = session
        self.memory = memory
        self.metacognition = Metacognition.coerce(metacognition)

        resolved_lobes: list[Lobe] = _resolve_blocks(lobes, Lobes.default)
        resolved_stages: list[Stage] = _resolve_blocks(stages, Stages.default)
        resolved_flows: list[Flow] = _resolve_blocks(flows, Flows.default)

        # The builtin default is the ported production network, whose intent
        # recognition uses the ported PathSpec recognizers; feed them to the
        # engine. Custom flows derive their PathSpecs from the façade Flows.
        resolved_paths = None
        if flows is None or flows == "default":
            from agent_sdk.preact.production import production_paths

            resolved_paths = production_paths()

        # Run plugins at assembly time. A plugin is a first-class plug-and-play
        # component: it contributes the full capacity surface (lobes/stages/flows/
        # paths/skills/tools) and may subtract a builtin it owns. A plugin carrying
        # ``enabled = False`` is skipped — disabling = not registered/resolvable.
        setup = AgentSetup()
        # The host a plugin may bind a stateful tool runtime to (agent-core passes
        # its interpreter). None for a bare agent.
        setup.host = host
        # ``plugins`` may be a list OR a PluginRegistry — a registry resolves to its enabled
        # (active) set, so enable/disable/override is managed there.
        active_plugins = plugins.active() if hasattr(plugins, "active") else list(plugins or [])
        # Grounding is opt-in (RagPlugin) — most agents have no retrieval. But
        # ``require_citations=True`` is an explicit grounding intent, so auto-enable
        # the RAG plugin (its finalize hook owns extraction + ground-or-refuse)
        # unless it is already present or a registry disabled it by name. The check is
        # ``isinstance`` (not name == "rag") so a host SUBCLASS that extends RagPlugin
        # — e.g. a KB-backed RagPlugin that also mounts retrieval tools — is recognized
        # and never double-installed (finalize hooks are not deduped).
        from agent_sdk.plugins.rag import RagPlugin

        if require_citations and not any(isinstance(p, RagPlugin) for p in active_plugins):
            disabled = plugins.is_disabled("rag") if hasattr(plugins, "is_disabled") else False
            if not disabled:
                active_plugins = [*active_plugins, RagPlugin()]
        for plugin in active_plugins:
            if getattr(plugin, "enabled", True) is False:
                continue
            plugin.install(setup)
            # A plugin OWNS its MCP server(s): declare a list (one or many) and they
            # all resolve+register under this one plugin — no separate MCP plugins.
            for server in getattr(plugin, "mcp_servers", None) or []:
                setup.add_mcp_server(server)
        resolved_lobes.extend(setup.lobes)
        # Dedup lobes by id (keep first): a plugin may re-contribute a lobe the
        # chosen network already carries (e.g. RagPlugin's ``cite`` on a minimal
        # network that still lists an inline cite) — one spec per id.
        _seen_lobe_ids: set[str] = set()
        resolved_lobes = [
            lb for lb in resolved_lobes
            if not (lb.id in _seen_lobe_ids or _seen_lobe_ids.add(lb.id))
        ]
        resolved_stages.extend(setup.stages)
        resolved_flows.extend(setup.flows)
        self._event_hooks = list(setup.event_hooks)
        self._pre_checks = list(setup.pre_checks)
        self._post_checks = list(setup.post_checks)
        self.workspace = setup.workspace

        # Skills: builtin/explicit + plugin-contributed (Skill façade objects). An
        # EXPLICIT skill overrides a plugin-contributed one of the same id (the host's
        # DB/override layer wins) — dedup by id keeping the first (explicit) occurrence.
        _seen_skill_ids: set[str] = set()
        resolved_skills: list[Any] = []
        for _sk in list(skills or []) + list(setup.skills):
            _sid = getattr(_sk, "id", None)
            if _sid is not None and _sid in _seen_skill_ids:
                continue
            if _sid is not None:
                _seen_skill_ids.add(_sid)
            resolved_skills.append(_sk)

        # Recognizer specs for plugin-contributed flows/paths. The default network
        # passes explicit ported recognizers (Engine then ignores flow-derived
        # paths), so derive specs for plugin flows and append them; on a custom
        # network Engine derives from all flows, so take over derivation once to
        # include explicit ``add_path`` specs too.
        if setup.flows or setup.paths:
            if resolved_paths is None:
                resolved_paths = _derive_path_specs(resolved_flows, resolved_stages) + list(
                    setup.paths
                )
            else:
                resolved_paths = (
                    list(resolved_paths)
                    + _derive_path_specs(setup.flows, resolved_stages)
                    + list(setup.paths)
                )

        # Honor plugin removals after every plugin installed. A pinned lobe is
        # never dropped — the output-contract lobes (``PINNED_LOBES`` = cite/filter)
        # and any ``spec.pinned`` lobe (synthesize) survive, so the citation/
        # grounding contract holds even if a plugin tries to subtract them. Path/
        # flow names share a namespace, so a single remove subtracts from both axes.
        if setup.removed_lobes:
            from agent_sdk.contracts.pins import PINNED_LOBES

            resolved_lobes = [
                lb
                for lb in resolved_lobes
                if lb.spec.id not in setup.removed_lobes
                or lb.spec.pinned
                or lb.spec.id in PINNED_LOBES
            ]
        removed_names = setup.removed_paths | setup.removed_flows
        if removed_names:
            resolved_flows = [f for f in resolved_flows if f.id not in removed_names]
            if resolved_paths is not None:
                resolved_paths = [p for p in resolved_paths if p.name not in removed_names]
        if setup.removed_skills:
            resolved_skills = [s for s in resolved_skills if s.id not in setup.removed_skills]

        # Universal memory (ON by default — native efficiency): a two-tier store (flash + long-term)
        # whose recall/note tools let the agent memorize durable facts and read offloaded detail back.
        # The funnel offloads spent tool bodies into it. A memory directive is injected so the agent
        # uses it natively. Opt out with universal_memory=False (the bare, memoryless behavior).
        self._memory_store = None
        recall_runtime = None
        if universal_memory:
            from agent_sdk.memory.recall_tool import RecallToolRuntime
            from agent_sdk.memory.universal import MemoryStore

            self._memory_store = MemoryStore(embed=embed)
            recall_runtime = RecallToolRuntime(self._memory_store)

        # MCP servers (constructor + plugins) → MCPToolRuntimes, resolved (connect +
        # discover) in the resolve phase before the first turn; their discovered tools
        # then register through the normal composite. Coerced specs/runtimes alike.
        from agent_sdk.mcp import MCPToolRuntime

        mcp_runtimes = [
            s if isinstance(s, MCPToolRuntime) else MCPToolRuntime(s)
            for s in (list(mcp_servers or []) + list(setup.mcp_servers))
        ]

        # Compose the tool runtime: @tool fns + ToolRuntimes + plugin tools + MCP + memory + recall.
        memory_runtime = memory.tool_runtime() if memory is not None else None
        self._memory_runtime = memory_runtime
        extra_runtimes = list(mcp_runtimes) + [r for r in (recall_runtime,) if r is not None]
        tool_runtime = self._compose_tools(
            list(tools or []) + list(setup.tools) + extra_runtimes,
            memory_runtime,
            priority_runtimes=list(setup.tool_runtimes),
        )
        # Any tool runtime that needs an async connect/discover phase (MCP) — resolved
        # once before the first turn (see ``_resolve_mcp`` / ``connect``).
        self._mcp_runtimes = [
            r for r in self._runtime_members(tool_runtime) if hasattr(r, "resolve")
        ]
        self._mcp_resolved = False

        # Prefetch hooks (plugins + the built-in always-on memory index). The
        # memory hook loads the store into TurnContext.memory_items at turn start
        # so the memory_recall lobe renders the ``## Memory`` index — most recalls
        # then need zero tool calls. No-op for an empty store (byte-identical).
        prefetch_hooks = list(setup.prefetch_hooks)
        if memory is not None:
            from agent_sdk.memory.prefetch import memory_prefetch_hook

            prefetch_hooks.append(memory_prefetch_hook(memory))

        self.engine = Engine(
            client=self.client,
            lobes=resolved_lobes,
            stages=resolved_stages,
            flows=resolved_flows,
            paths=resolved_paths,
            skills=resolved_skills,
            tools=tool_runtime,
            instructions=instructions,
            weights=weights,
            budgets=engine_budgets,
            metacognition=self.metacognition,
            memory=memory,
            memory_runtime=memory_runtime,
            memory_store=self._memory_store,
            system_addendum=(MEMORY_DIRECTIVE if universal_memory else ""),
            require_citations=require_citations,
            share_history=share_history,
            tools_in_prompt=tools_in_prompt,
            funnel=funnel,
            tz=tz,
            lang=lang,
            prompt_format=prompt_format,
            context=context,
            pre_turn_gate=pre_turn_gate,
        )
        self.engine._prefetch_hooks = prefetch_hooks
        self.engine._tool_filters = list(setup.tool_filters) + list(tool_filters or [])
        self.engine._finalize_hooks = list(setup.finalize_hooks)
        self.engine._tool_result_hooks = list(setup.tool_result_hooks)

        self._last_trace: Trace | None = None
        self._jobs: dict[str, asyncio.Queue] = {}

    # ── tool composition ─────────────────────────────────────────────────────
    @staticmethod
    def _compose_tools(
        tools: list[Any], memory_runtime: Any, *, priority_runtimes: list[Any] | None = None
    ) -> Any:
        from agent_sdk.contracts.tools import CompositeToolRuntime
        from agent_sdk.tools import FunctionToolRuntime

        fn_tools: list[Any] = []
        runtimes: list[Any] = []
        for t in tools:
            if hasattr(t, "get_tool_specs") and hasattr(t, "call_tool"):
                runtimes.append(t)
            else:
                fn_tools.append(t)
        # Plugin-mounted whole runtimes go first so a namespaced surface (kb.*)
        # wins the first-seen-name dedup over @tool fns and other runtimes.
        composed: list[Any] = list(priority_runtimes or [])
        if fn_tools:
            composed.append(FunctionToolRuntime(fn_tools))
        composed.extend(runtimes)
        if memory_runtime is not None:
            composed.append(memory_runtime)
        if not composed:
            return None
        return CompositeToolRuntime(composed)

    @staticmethod
    def _runtime_members(tool_runtime: Any) -> list[Any]:
        """The individual runtimes inside the (possibly composite) tool runtime."""
        if tool_runtime is None:
            return []
        members = getattr(tool_runtime, "_runtimes", None)
        return list(members) if members is not None else [tool_runtime]

    # ── MCP resolve phase ─────────────────────────────────────────────────────
    async def _resolve_mcp(self) -> None:
        """Connect + discover every MCP server once, before the first turn. Each runtime
        checks status (``initialize``); if connected it discovers the schema (``tools/list``)
        and its tools register through the composite. A server that fails to connect simply
        contributes nothing (its ``.error`` is set). Idempotent + concurrent."""
        if self._mcp_resolved:
            return
        self._mcp_resolved = True
        pending = [r for r in self._mcp_runtimes if not getattr(r, "_resolved", False)]
        if pending:
            await asyncio.gather(*(r.resolve() for r in pending))

    async def connect(self) -> dict[str, bool]:
        """Eagerly run the MCP resolve phase (otherwise it runs lazily on the first turn).
        Returns ``{server_name: connected}`` for inspection."""
        await self._resolve_mcp()
        return {
            getattr(r, "name", type(r).__name__): bool(getattr(r, "connected", False))
            for r in self._mcp_runtimes
        }

    # ── core turn plumbing ───────────────────────────────────────────────────
    async def _run_stream(self, input: str, session: Session | None) -> AsyncIterator[Any]:
        await self._resolve_mcp()  # connect + discover MCP servers, then register their tools
        for check in self._pre_checks:
            check(input)  # a guardrail raises to block the turn
        sess = session or self.session
        state = await sess.load() if sess is not None else SessionState()
        # Stateless seam: rebind the (per-agent) universal memory store to THIS session's snapshot
        # carried on the state, so a turn never sees another session's memory. Only when a session
        # is in play — the sessionless in-process path keeps accumulating on the agent as before.
        if self._memory_store is not None and sess is not None:
            self._memory_store.restore(state.memory)
        last_result: AgentResult | None = None
        async for ev in self.engine.stream(input, state):
            for hook in self._event_hooks:
                with contextlib.suppress(Exception):
                    hook(ev)
            if isinstance(ev, Final):
                last_result = ev.result
            yield ev
        if last_result is not None:
            for check in self._post_checks:
                check(last_result)  # a guardrail raises to block the result
            self._last_trace = last_result.trace
            # Establish: reliably offload the facts the user stated this turn (native memorize).
            if self._auto_establish and self._memory_store is not None:
                from agent_sdk.memory.establish import fact_key, salient_facts

                for fact in salient_facts(input):
                    # Topic-keyed: a newer version of the same fact consolidates over the old one.
                    self._memory_store.remember(
                        "fact", fact, scope="conversation", key=fact_key(fact), source="establish"
                    )
            if sess is not None:
                # Persist the WHOLE state when the store supports it (history + memory +
                # skills_in_use + meta_flow_bias, atomically) — branch FIRST so we only mutate
                # state.history on the save path. Stores that only ``append`` (host-owned
                # durability, e.g. the Mezon worker) take the legacy two-append path untouched.
                if getattr(sess.store, "save", None) is not None:
                    state.history.append(Turn("user", input))
                    state.history.append(Turn("assistant", last_result.text))
                    if self._memory_store is not None:
                        state.memory = self._memory_store.to_json()
                    await sess.save(state)
                else:
                    await sess.append(Turn("user", input))
                    await sess.append(Turn("assistant", last_result.text))

    # ── public API ───────────────────────────────────────────────────────────
    async def query(self, input: str, *, session: Session | None = None) -> AgentResult:
        result: AgentResult | None = None
        async for ev in self._run_stream(input, session):
            if isinstance(ev, Final):
                result = ev.result
        assert result is not None
        return result

    def act(self, input: str, *, session: Session | None = None) -> AgentStream:
        return AgentStream(self._run_stream(input, session))

    async def run_snapshot(
        self, input: str, snapshot: dict | None = None
    ) -> tuple[AgentResult, dict]:
        """Run one turn STATELESSLY: restore from a plain-JSON ``snapshot``, run, and return
        ``(result, next_snapshot)``. No ``Session``/store wiring — persist the returned dict
        wherever you like (Redis, a DB, a file) and hand it back next turn.

        This is the easy path for stateless serving: a process holds only the (immutable) agent
        config and carries ALL per-session state in the snapshot, so any worker/replica can serve
        any session and a restart loses nothing. The snapshot is ``SessionState.to_json()`` — a
        versioned, forward/backward-tolerant schema (see ``SNAPSHOT_VERSION``).

        Concurrency note: one agent instance runs one turn at a time (it rebinds its working memory
        to the snapshot per call). To serve sessions concurrently, give each in-flight turn its own
        agent — e.g. ``AgentWorker(agent_factory=…)`` — so snapshots never interleave on a shared
        store.
        """
        from agent_sdk.session import Session, SessionState

        class _SnapshotStore:
            """Holds one state in memory for the duration of a single stateless turn."""

            def __init__(self, state: SessionState) -> None:
                self._state = state

            async def load(self, id: str) -> SessionState:  # noqa: A002 - protocol name
                return self._state

            async def save(self, id: str, state: SessionState) -> None:  # noqa: A002
                self._state = state

        store = _SnapshotStore(SessionState.from_json(snapshot or {}))
        result = await self.query(input, session=Session("snapshot", store))
        return result, (await store.load("snapshot")).to_json()

    def inspect(self, input: str) -> ActivationSnapshot:
        return self.engine.inspect(input)

    @property
    def last_trace(self) -> Trace | None:
        return self._last_trace

    def suggest_optimizations(self) -> list[Optimization]:
        trace = self._last_trace
        if trace is None:
            return []
        out: list[Optimization] = []
        for stage in trace.flow_stages:
            flow = str(stage.get("flow") or "")
            name = str(stage.get("stage") or "")
            produced = any(
                s.get("kind") == "answer" and s.get("text") for s in stage.get("steps", [])
            )
            if flow and name and not produced and not stage.get("skipped"):
                out.append(
                    Optimization(
                        axis="flow",
                        target=f"{flow}.{name}",
                        reason="stage produced no answer text",
                        weight_patch={f"flow_{flow}__step_{name}__disable": 1.0},
                    )
                )
        return out

    def spec(self) -> Any:
        from agent_sdk.spec import build_spec

        return build_spec(self)

    @classmethod
    def from_spec(
        cls, spec: Any, *, client: Any, tools: list[Any] | None = None, **overrides: Any
    ) -> PreactAgent:
        from agent_sdk.spec import agent_from_spec

        return agent_from_spec(spec, client=client, tools=tools, **overrides)

    def with_(self, **overrides: Any) -> PreactAgent:
        return PreactAgent(**{**self._config, **overrides})

    # ── serving (in-process queue) ───────────────────────────────────────────
    async def submit(self, input: str, *, session: Session | None = None) -> str:
        job_id = uuid.uuid4().hex[:16]
        queue: asyncio.Queue = asyncio.Queue()
        self._jobs[job_id] = queue

        async def _run() -> None:
            try:
                async for ev in self._run_stream(input, session):
                    await queue.put(ev)
            finally:
                await queue.put(_DONE)

        asyncio.ensure_future(_run())
        return job_id

    async def events(self, trace_id: str) -> AsyncIterator[Any]:
        queue = self._jobs.get(trace_id)
        if queue is None:
            return
        while True:
            ev = await queue.get()
            if ev is _DONE:
                break
            yield ev
        self._jobs.pop(trace_id, None)
