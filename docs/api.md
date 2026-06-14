# PreAct SDK — public API

> **Status: design / target API.** This is the agreed surface for the standalone
> `agent_sdk`. The building blocks it composes (lobes, stages, flows, skills, activation
> network, metacognition, inspection) exist today; the **`PreactAgent` façade and the generic
> `Engine` kernel are being built** (see [Roadmap](#roadmap)). Import paths below use
> `agent_sdk` (the post-extraction name); pre-extraction they live under `agent_core.sdk`.

PreAct is *pre-structured acting*: the agent doesn't free-act by letting the model pick tool
calls turn by turn (vanilla ReAct) — its acting is shaped by a deliberate thinking model
(layered **lobes** → reusable **stages** → intent **flows**), with metacognition supervising.
See [`preact.md`](./preact.md).

---

## 1. Quickstart

```python
from agent_sdk import PreactAgent, Lobes, Stages, Flows, Stage, Flow, Skill, Memory, tool
from agent_sdk.clients import AnthropicClient

@tool
async def search(query: str, top_k: int = 5) -> str:
    "Search the knowledge base."          # docstring → description; signature → JSON schema

agent = PreactAgent(
    client=AnthropicClient("claude-opus-4-6"),
    instructions="You are a helpful research assistant.",
    tools=[search],
    # lobes / stages / flows default to the built-in PreAct network when omitted
)

# one-shot
result = await agent.query("What changed in v2?")
print(result.text)

# streaming
async for event in agent.act("What changed in v2?"):
    print(event)
```

That's the floor. Everything below is opt-in: custom lobes/stages/flows, skills, memory,
persistence, metacognition, multi-provider routing, probing, and queue-based serving.

---

## 2. `PreactAgent`

```python
agent = PreactAgent(
    client=AnthropicClient("claude-opus-4-6"),  # an LLM client class (§7), or "claude-…" shorthand
    instructions="…",                           # system persona (a stable prompt contribution)

    # ── building blocks (omit → built-in PreAct defaults) ──
    lobes=Lobes.default(),                       # list[Lobe] | LobeRegistry | "default"
    stages=Stages.default(),                     # list[Stage] | StageRegistry | "default"
    flows=Flows.default(),                       # list[Flow] | FlowRegistry | "default"
    skills=[Skill(...)],                         # list[Skill]
    tools=[search],                              # list[@tool fns] | ToolRuntime

    # ── persistence seams (pluggable; in-memory defaults) — §6 ──
    session=Session(id="conv-42", store=SessionStoreRedis(url)),  # persisted conversation state
    memory=Memory(store=MemoryStoreRedis(url)),  # durable agent memory (the `memory` tool)

    # ── extensions — §5 Plugins (workspace, MCP, telemetry, guardrails, …) ──
    plugins=[PluginWorkspace(driver="virtual"), PluginOTel()],

    # ── reasoning control ──
    metacognition=Metacognition(mode="apply"),   # a class (§8); "apply"/"observe" shorthand ok
    weights={"prior_research": 0.2},             # sparse activation overlay (per-bot tuning)
    budgets={"context_tokens": 8000},            # per-layer / per-stage budgets
    prompt_format="xml",                         # default: XML-tagged context (Claude-Code-style);
                                                 # "markdown" to opt out
)
```

**Prompt format.** By default the system prompt composes each context section as an XML tag
(`<instructions>`, `<conversation>`, `<memory>`, `<tools>`, `<skills>`, `<notes>`, `<env>`, …) —
Claude (and Claude Code) parse XML-delimited context far more reliably than flat markdown, so this
lifts comprehension and accuracy at a negligible token cost. Provenance is preserved (the viewer
still colours by source). Pass `prompt_format="markdown"` for the older flat layout.

### Methods

| Call | Returns | Notes |
|---|---|---|
| `await agent.query(input, *, session=None)` | `AgentResult` | one-shot; the full turn |
| `agent.act(input, *, session=None)` | `AgentStream` | streaming; async-iterable **and** awaitable |
| `agent.inspect(input)` | `ActivationSnapshot` | **no LLM** — dry, deterministic routing probe (§9) |
| `agent.last_trace` | `Trace` | full trace of the last run |
| `agent.suggest_optimizations()` | `list[Optimization]` | weight patches from the last trace (§9) |
| `agent.spec()` | `PreactSpec` | serializable config (§10) |
| `agent.with_(**overrides)` | `PreactAgent` | immutable copy (A/B; e.g. `with_(session=…)`) |
| `await agent.submit(input, *, session)` | `str` (trace_id) | enqueue a turn for the worker pool (§11) |
| `agent.events(trace_id)` | `AsyncIterator[AgentEvent]` | subscribe to a submitted turn's events (§11) |

The agent's bound `session` (from the constructor) is the default. A **server handling many
conversations** keeps one agent and selects the conversation per call by passing
`session=Session(id=…, store=shared_store)` (or `agent.with_(session=…)`); omit `session` entirely
for a stateless turn.

---

## 3. Results

```python
@dataclass
class AgentResult:
    text: str                                   # the answer (status == "answered")
    status: Literal["answered", "refused"]
    citations: list[Citation]                   # grounding (chunk_id, source_ref, span)
    refusal: Refusal | None                     # .reason + user-facing .message
    usage: Usage                                # input/output/cache tokens + estimated cost
    memory_updates: list[MemoryUpdate]          # structured {action, scope, key}
    trace: Trace                                # see §9
    def __str__(self) -> str: ...               # → text
```

`AgentResult` is the ergonomic wrapper over the engine's `FinalEnvelope`
(`contracts/memo.py`) plus the `Trace`.

---

## 4. Streaming & typed events

`agent.act(...)` returns an `AgentStream` — an async-iterable of **typed** events that is also
awaitable to the final result (à la the Vercel AI SDK / Pydantic AI `run_stream`):

```python
stream = agent.act("…")

async for ev in stream:                 # typed events (pattern-matchable)
    match ev:
        case TextDelta(text):           print(text, end="")
        case ToolCall(name, input):     log(f"→ {name}({input})")
        case CitationFound(citation):   cite(citation)
        case Final(result):             save(result)

# or just the text:
async for chunk in stream.text_stream:  print(chunk, end="")

# or skip events and await the result:
result = await stream                   # == await stream.result()
```

### Event union

```python
class AgentEvent: ...                    # sealed; every event carries trace_id + ts
RunStart(trace_id)                       PathResolved(path, score)
StageStart(flow, stage)                  TextDelta(text)
ToolCall(id, name, input)                ToolResult(id, name, output)
CitationFound(citation)                  MetaAction(action, reason)
StageEnd(flow, stage, usage)             Final(result: AgentResult)
```

Events serialize 1:1 to JSON (`ev.to_json()`) for SSE / pub-sub transport — the same wire shape
the worker already publishes (`partial`/`stage_start`/`tool_call`/`citation`/`answer`). The JSON
schema is in [`porting.md`](./porting.md).

---

## 5. Building blocks

Every building block — **Lobes, Stages, Flows, and Skills** — is `Activable`: it shares **one
interface** so the framework reads uniformly, activates by the same rule, and serializes
identically:

```python
@runtime_checkable
class Activable(Protocol):
    id: str                              # stable identifier
    name: str                            # display name
    description: str                     # WHAT it is (one line)
    use_when: str                        # WHEN — natural-language trigger (doc + semantic activation)
    def signal(self, ctx: Context) -> float: ...   # the deterministic, free activation (0 = dark)
```

`signal` is free and deterministic — never an LLM call. `use_when` doubles as documentation and
the source for optional semantic/LLM-assisted activation. Same five fields everywhere → a
**Lobe**'s signal gates its context, a **Stage**'s signal gates the step, a **Flow**'s signal
recognizes the intent, and a **Skill**'s signal selects the skill — one mental model, one
inspect output, one spec shape.

### Lobes (context — OY axis)

A lobe is a passive context worker (what fires into the window). Author it as a class or a
decorator:

```python
from agent_sdk import Lobe, lobe, Layer

class Classify(Lobe):
    id, name = "classify", "Classify"
    description = "Route the turn simple vs complex."
    use_when = "every answer-producing turn"
    layer = Layer.COGNITION              # B2 MEMORY · B3 SKILL · B4 COGNITION · B5 EXPRESSION
    behavior = "select"
    writes = ("route",)                  # node kinds it writes to the blackboard
    excites = {"plan": 0.3}              # forward edges (lobe_id → weight)
    def signal(self, ctx) -> float: return 1.0

@lobe(id="greet", layer=Layer.EXPRESSION, use_when="a greeting")
async def greet(ctx) -> LobeResult: ...  # decorator form for simple lobes

Lobes.default()                          # the built-in B2–B5 set; compose or extend your own
```

`cite` and `filter` are **pinned** (`pinned=True`) — the activation network can never deactivate
them (ground-or-refuse). See `PINNED_LOBES`.

### Stages (execution units — first-class, reusable, `Activable`)

A **stage** is one execution unit: a slice of lobes it consults, a loop mode, and its tools.
Like a Lobe or a Skill, a Stage is `Activable` — it carries `id` / `name` / `description` /
`use_when` / `signal`, and its `signal` gates whether the step runs this turn. Author it as a
class or with the concise builder:

```python
from agent_sdk import Stage, stage

# class form — the full Activable surface (mirrors Lobe authoring)
class Research(Stage):
    id, name = "research", "Research"
    description = "Gather evidence from sources."
    use_when = "the question needs external facts"
    lobes = ("research",)
    loop = "agentic"                     # none | single | agentic | map
    tools = ("search",)
    def signal(self, ctx) -> float:      # deterministic, free — gates the step (0 = skip)
        return 1.0 if ctx.get("needs_sources") else 0.0

# decorator / concise form for simple stages (signal defaults to always-on)
stages = [
    stage("plan",       lobes=["plan"]),
    Research(),
    stage("synthesize", lobes=["synthesize", "cite", "filter"]),
    stage("clarify",    lobes=["clarify"], use_when="an ambiguous follow-up"),
]
```

`loop` ∈ `none` (pure prompt) · `single` (one LLM call) · `agentic` (a ReAct `tool_loop`) ·
`map` (fan-out over a scratchpad key). Per-stage overrides: `model`, `temperature`, `max_tokens`,
`hops`, `system_prompt`. Because the Stage's `signal` is part of the `Activable` contract, a flow
can list a stage that only fires under its own condition — same gating rule as lobes and skills.

A `loop="map"` stage fans out one scoped sub-execution per work-item in `scratchpad[fanout_key]`.
Three fan-out knobs (all default to today's behavior): `fanout_parallel` (run workers concurrently
via `asyncio.gather`, semaphore-bounded; default sequential with state-carry), `fanout_max` (the
concurrency / item cap, ≤ 40), and `fanout_isolated` (each worker gets a fresh evidence pool — no
cross-worker leakage; default shares the turn pool). Either shape is bounded-failure: a worker that
raises or exceeds a per-item `timeout` is recorded `status="failed"`, never dropped. See
**Subagents** below and `docs/concepts/12-subagent-fanout.md`.

#### How an `agentic` loop ends

A `loop="agentic"` stage runs hop-by-hop until one of these terminates it:

- **`end_turn`** — the model stopped calling tools and produced its answer (the normal case).
- **`hops` ceiling** — the per-stage hop budget is reached; the final hop is forced tool-free so
  the model must answer with what it has.
- **stall-break** (opt-in `budgets={"stall_patience": N}`) — `N` consecutive hops that produced no
  *new, non-error* tool result (repeated reads, errors, refused writes) are treated as no progress;
  the model is steered to converge and the next hop is forced tool-free. Progress is measured on
  **world-state delta**, not byte-identical calls.
- **`max_tokens` truncation** — a response cut off at the token cap is **not** a clean end. The hop
  is retried with a doubled budget (`budgets={"truncation_retries": 2, "truncation_token_cap":
  16000}`) before the stage gives up; if it still truncates, the stage ends but the trace records a
  `truncated_final` meta-action so a half-finished answer isn't mistaken for a complete one.

Relevant `budgets` knobs: `stall_patience` (off by default), `enforce_tool_allowlist` (when set, the
runtime *refuses to execute* a tool outside the active stage's `tools` allowlist — `memory`/`recall`
essentials always pass — so the allowlist is a real boundary, not just a hidden-from-prompt hint),
`truncation_retries`, `truncation_token_cap`. `default_max_tokens` is `4096` (thinking models can
burn >1k tokens reasoning before a tool call).

### Flows (intent pipelines — combination of stages by reference)

A **flow** is an ordered list of Stage **ids** — the same stage is freely combined into many
flows, never bound to one:

```python
from agent_sdk import Flow

flows = [
    Flow("research", use_when="multi-step questions needing sources",
                     stages=["plan", "research", "synthesize"]),
    Flow("qna",      use_when="a direct question",        stages=["synthesize"]),
    Flow("clarify",  use_when="an ambiguous follow-up",   stages=["clarify", "synthesize"]),
]
```

A Flow is `Activable` — its `signal`/`use_when` recognize the turn's intent (this replaces the
separate "path recognizer"; the highest-scoring flow over threshold wins, else *emergent*).
`FlowRegistry` resolves the id references against the `StageRegistry` at run time, so editing a
stage updates every flow that uses it.

### Skills (procedural knowledge, progressively disclosed — `Activable`)

A Skill is `Activable` too: `when` is its `use_when`, and its `signal` is what the skill-select
step uses to decide whether to surface it this turn (the same gating rule as lobes and stages).

```python
from agent_sdk import Skill

Skill(
    id="code_review",
    when="reviewing pull requests",       # → use_when (the activation trigger)
    instructions="Check logic, tests, security…",
    tools=["search"],
    disclosure="on_demand",               # "eager" (inline) | "on_demand" (model calls skill.read)
    files={"GUIDE.md": "## Deep checklist …"},   # layered reading for on_demand
    # signal: defaults to use_when-driven selection; override for a deterministic gate
)
```

Skills are Standard Operating Procedures the agent runs: the `SKILL.md` standard, the
folder/section/ToC chunking, the activation strategies, the skill lobe state machine, and
how a skill's content is injected back into context — full reference in
[`concepts/09-skills.md`](concepts/09-skills.md).

### Subagents (named fan-out workers — Claude Code's `.claude/agents/*.md`)

A **`Subagent`** is a named, reusable scoped worker — the typed form of a `map` work-item:
`name` + `description` (when to delegate) + `instructions` (its system prompt) + a restricted
`tools`/`lobes` belt + `model`/`max_tokens`/`hops` budget. It runs in its own context and returns a
compressed result, never its raw working set. Define once, delegate by name many times.

```python
from agent_sdk import Subagent, SubagentRegistry, load_agents_dir
from agent_sdk.plugins.subagents import SubagentsPlugin

# in code …
registry = SubagentRegistry([
    Subagent("reviewer", description="reviews code for bugs", instructions="You REVIEW code.",
             tools=["read", "grep"]),
])
registry.add_row({"name": "tester", "description": "writes tests"})   # declarative row

# … or from .claude/agents/*.md (frontmatter: name/description/tools/model; body = prompt)
registry = SubagentRegistry(load_agents_dir(".claude/agents"))

agent = PreactAgent(client=…, instructions="…",
                    plugins=[SubagentsPlugin(registry)])   # or SubagentsPlugin(agents_dir="…")
```

`SubagentsPlugin` wires the registry into the metacognition `meta_control(action=fan_out)` enactor:
the model delegates by name (`items=[{"agent": "reviewer", "input": "review module X"}]`), the
enactor resolves the name deterministically (unknown name → a clear tool error), and the
`meta_fanout` stage runs the workers **parallel + context-isolated**. A `subagent_catalog` lobe
surfaces the available subagents to the reflect step. Routing stays deterministic — no LLM judges
the pipeline; the model only *names* a subagent inside the existing `meta_control` call. `cite` /
`filter` ground the *aggregated* results — grounding is never a worker's decision. Full model:
[`concepts/12-subagent-fanout.md`](concepts/12-subagent-fanout.md).

### Tools — the `@tool` decorator

Turn a typed function into a tool; the SDK introspects the signature/types/docstring into an
Anthropic-compatible spec and wraps it in a `ToolRuntime`:

```python
from agent_sdk import tool
from pydantic import BaseModel

@tool
async def search(query: str, top_k: int = 5) -> str:
    "Search the knowledge base."          # docstring → description

class Ticket(BaseModel): title: str; priority: int = 3

@tool(name="tickets.create", requires=["acl"])     # explicit name + identity requirement
async def create_ticket(args: Ticket) -> str: ...   # Pydantic args → JSON schema

agent = PreactAgent(client=…, tools=[search, create_ticket])
```

Sync or async functions both work. For MCP servers or custom transports, pass an object
implementing the `ToolRuntime` protocol (`contracts/tools.py`) directly in `tools=[…]`; the agent
composes everything via `CompositeToolRuntime`.

### Plugins (extensions)

A **plugin** is a first-class plug-and-play component that extends the agent at assembly time.
It may contribute the **full capacity surface** — lobes, stages, paths/flows, skills, and tools
— plus event hooks, guardrails, and seam bindings (like a filesystem). It's the single,
composable extension mechanism: `plugins=[…]`. Enabled (present in the list) ⇒ its capabilities
are registered/resolvable; absent or `enabled = False` ⇒ not. See the deep-dive at
[`concepts/10-plugins.md`](concepts/10-plugins.md).

The **core** network (cognition, tools, skills, task, memory, reply) lives in `agent_sdk/lobes/`
and is not a plugin. Plugins are the *extension* layer: two default-on but toggleable ones
(`SafetyPlugin` — `cite`/`filter` grounding; `FormatPlugin` — output styling) plus opt-in
integrations. Manage them with a `PluginRegistry` (register / override / enable / disable),
which `PreactAgent(plugins=…)` accepts in place of a list.

```python
@runtime_checkable
class Plugin(Protocol):
    name: str
    def install(self, setup: AgentSetup) -> None: ...
```

`AgentSetup` is the builder a plugin fills — the full surface plus removals:

```python
setup.add_lobe(lobe)      setup.add_stage(stage)    setup.add_flow(flow)
setup.add_path(path)      setup.add_skill(skill)    setup.add_tool(tool)
setup.on_event(hook)      setup.add_pre_check(fn)   setup.add_post_check(fn)
setup.add_tool_filter(f)  setup.add_prefetch_hook(h) setup.bind_workspace(ws)
# subtract a builtin this plugin owns/overrides (pinned cite/filter/synthesize always survive):
setup.remove_lobe(id)     setup.remove_path(name)   setup.remove_flow(name)   setup.remove_skill(slug)
```

Built-in plugins:

```python
from agent_sdk.plugins import (
    SafetyPlugin, FormatPlugin,                 # default-on, toggleable (grounding / styling)
    TaskPlugin, MetacognitionPlugin,            # opt-in capability plugins
    PluginWorkspace, PluginMCP, PluginOTel, PluginGuardrails, PluginSupportTriage,
    PluginRegistry, builtin_registry,
)

SafetyPlugin()                                 # cite/filter grounding (default-on; disable for non-RAG)
FormatPlugin()                                 # channel/language/tone styling (default-on)
TaskPlugin()                                   # todo-driven task execution (plan→execute→deliver)
MetacognitionPlugin()                          # think-about-thinking: meta_context lobe + meta_reflect
                                               #   stage + meta_control tool (pick skills / bias flow /
                                               #   fan out / trim-skip); opt-in, traced, cite/filter pinned
PluginWorkspace(driver="virtual")              # a virtual FS + fs.* tools (read/write/list/edit)
PluginWorkspace(driver="local", root="/data/agent-fs")    # persisted to disk
PluginWorkspace(driver="s3", bucket="…")
PluginMCP(spec={"name": "wx", "transport": "http", "endpoint": "https://…/mcp",
                "auth_type": "bearer", "auth": "…"})   # connect → discover schema → register tools
PluginMCP(url="https://…/mcp")                 # mount an external MCP server's tools (static specs)
PluginOTel()                                   # OpenTelemetry traces/metrics via event hooks
PluginGuardrails(pre=[…], post=[…])            # pre/post turn checks
PluginSupportTriage()                          # worked example: lobe+stage+flow+skill+tool at once
```

`PluginWorkspace` gives the agent a persistent, sandboxed file tree for artifacts and working
documents and wires the `fs.read`/`fs.write`/`fs.list`/`fs.edit` tools + the heavy-document path
(`react/docworkspace`). Its `driver` selects the backend (`virtual` ephemeral · `local` disk ·
`s3`), each implementing the `Workspace` protocol:

```python
class Workspace(Protocol):                      # the seam a workspace driver binds
    async def read(self, path) -> bytes
    async def write(self, path, data) -> None
    async def list(self, prefix="") -> list[str]
    async def edit(self, path, patch) -> None
```

Write your own plugin to ship a reusable capability pack — a whole behavior (tools + a lobe +
a stage + an intent flow + a skill) as one installable object:

```python
class WeatherPlugin:
    name = "weather"
    def install(self, setup):
        setup.add_tool(get_forecast)            # an @tool
        setup.add_lobe(WeatherLobe())           # context/behavior
        setup.add_stage(forecast_stage)         # an execution unit
        setup.add_flow(weather_flow)            # a new intent path (recognized for matching turns)
        setup.add_skill(weather_skill)          # procedural knowledge
        setup.on_event(lambda ev: ...)          # observability
agent = PreactAgent(client=…, plugins=[WeatherPlugin()])
```

Adding a `Flow` registers a recognizable intent **path** automatically (its `PathSpec` is derived
from the flow's stages), so a matching turn routes to it — `agent.inspect(query).path` reflects
the win. Removals are honored after every plugin installs, and **no weight or removal can drop a
pinned lobe** (`cite`/`filter`/`synthesize`), so the citation contract holds regardless.

---

## 6. Session & memory (pluggable, production-grade)

Two orthogonal seams, both pluggable; defaults are in-memory (zero infra), swap for
Redis/Postgres at scale. There is **no separate context store** — per-conversation injected
context lives in the **Session**, and durable cross-conversation context (profiles, rules) lives
in **Memory**'s `user`/`channel`/`bot` scopes. (A filesystem for artifacts/documents is added as a
**plugin** — see §5 `PluginWorkspace`.)

### Session — persisted conversation state

`Session` is a small handle bundling an `id` and a backing `store`; it carries the rolling
conversation (history + summary + extracted facts + any per-conversation injected context) and is
loaded at turn start, appended + compacted at turn end.

```python
from agent_sdk import Session
from agent_sdk.stores import SessionStoreInMemory, SessionStoreRedis, SessionStoreSQL

agent = PreactAgent(client=…, session=Session(id="conv-42", store=SessionStoreRedis(url)))

class SessionStore(Protocol):            # the pluggable backend
    async def load(self, id) -> SessionState                  # history + summary + facts + context
    async def append(self, id, turn: Turn) -> None
    async def compact(self, id, summarizer) -> None           # roll old turns into a summary
# built-ins: SessionStoreInMemory() · SessionStoreRedis(url) · SessionStoreSQL(dsn)
```

### Memory — durable agent memory (the `memory` tool)

```python
from agent_sdk import Memory
from agent_sdk.stores import MemoryStoreInMemory, MemoryStoreRedis

agent = PreactAgent(client=…,
    memory=Memory(store=MemoryStoreRedis(url), scopes=["conversation", "user", "bot"]))

class MemoryStore(Protocol):
    async def read(self, scope, key) -> Any
    async def write(self, scope, key, value) -> None
    async def search(self, scope, query, k=5) -> list[MemoryItem]
```
Scopes: `turn` (the always-on `Scratchpad`) · `conversation` · `channel` · `user` · `bot`.
Attaching `Memory` auto-wires the `memory` tool (remember / recall / forget within allowed
scopes). Durable profiles and rules are just `user`/`bot`-scoped memory.

---

## 7. LLM clients (multi-provider)

The model is a **client class** (a concrete `LlmCall`), not a fixed string:

```python
from agent_sdk.clients import AnthropicClient, OpenAIClient, MixedClient

PreactAgent(client=AnthropicClient("claude-opus-4-6", api_key=…))
PreactAgent(client=OpenAIClient("gpt-4.1", base_url=…, api_key=…))

# route per stage (and per provider) — cheap tier to classify, strong tier to synthesize:
PreactAgent(client=MixedClient(
    default=AnthropicClient("claude-opus-4-6"),
    classify=OpenAIClient("gpt-4o-mini"),
    synthesize=AnthropicClient("claude-opus-4-6"),
))
```

`AnthropicClient` / `OpenAIClient` own streaming, usage accounting, and retries. `MixedClient`
is a composite that dispatches on the call's `stage`. Custom providers implement `LlmCall`
(`contracts/llm.py`) or subclass `BaseClient`. A bare string (`client="claude-…"`) is shorthand
that builds the matching default client.

---

## 8. Metacognition (a class)

Metacognition is a first-class, subclassable object — it monitors the object-level state and
regulates the next step:

```python
from agent_sdk import Metacognition

agent = PreactAgent(..., metacognition=Metacognition(
    mode="apply",                        # "observe" (monitor+trace only) | "apply"
    apply_actions={"adjust_lobe_slice"}, # allow-list (also: skip_step, retry_step)
))

class DomainMeta(Metacognition):
    def monitor(self, snapshot) -> list[Observation]: ...   # custom signals
    def regulate(self, observations, *, stage, lobes) -> Decision: ...
agent = PreactAgent(..., metacognition=DomainMeta())
```

`cite`/`filter` stay pinned and never skippable regardless of a custom subclass — the engine
enforces it, not the metacognition object. Strings `"apply"`/`"observe"` are accepted as
shorthand.

---

## 9. Probe · inspect · benchmark

**Probe (no LLM)** — explain how a turn will route before spending a token:

```python
snap = agent.inspect("compare these two approaches and cite sources")
snap.path        # → ("research", 0.82)
snap.lobes       # → [{id, layer, activated, score, reason}, …]
snap.flow        # → resolved stage sequence
snap.budget      # → per-layer token budgets
snap.to_json()
```

**Trace (after a run)** — the full picture:

```python
t = result.trace
t.path; t.lobes; t.flow_stages; t.blackboard; t.usage
t.timeline()     # ReAct sub-steps (thinking / tool_use / tool_result / answer)
t.to_json()
```

**Optimize** — pure proposals you choose to apply:

```python
for opt in agent.suggest_optimizations():
    print(opt.axis, opt.target, opt.reason, opt.weight_patch)
agent2 = agent.with_(weights={**agent.spec().weights, **opt.weight_patch})  # A/B
```

**Benchmark** — a thin harness over scenarios + trace assertions:

```python
from agent_sdk.bench import Harness, Scenario

report = await Harness(agent).run([
    Scenario(input="compare A and B", expect_path="research"),
    Scenario(input="hello", expect_path="relational"),
])
report.summary()   # path_accuracy, lobe_recall/noise, token_efficiency, p95_latency
```

These wrap the existing `inspection.py` snapshots and the attentionbench/flowbench/skillbench
trace-reading patterns into one public surface.

---

## 10. Serializable spec (portability)

The whole PreAct configuration is data:

```python
spec = agent.spec()                 # PreactSpec: lobes, stages, flows, weights, budgets, skills
spec.to_json()                      # JSON-Schema-validated → preact.spec.json
agent2 = PreactAgent.from_spec(spec, client=AnthropicClient(…), tools=[search])
```

The deterministic **core** (intent recognition, activation, attention/budget, flow resolution) is
a pure function of `(spec, context)` — no I/O, no LLM. That is what makes ports tractable: a
Rust/Go/JS implementation re-creates the core from the spec and wires only the I/O seams
(`LlmCall`, `ToolRuntime`, `Embed`, the stores). Full schemas + the activation algorithm are in
[`porting.md`](./porting.md).

---

## 11. Serving at scale (message queue + pub-sub)

`query()`/`act()` are the direct in-process path. For production, run a worker pool that drains a
queue and streams events over pub-sub:

```python
from agent_sdk.serve import AgentWorker, RedisQueue, RedisEventSink, RedisLock

# producer side (your API): enqueue a turn, stream its events from anywhere
trace_id = await agent.submit("research X", session=Session(id="s1", store=shared_store))
async for ev in agent.events(trace_id): ...

# consumer side (the scalable server): drain → run → publish
worker = AgentWorker(
    agent, queue=RedisQueue(url), sink=RedisEventSink(url),
    concurrency=8, session_lock=RedisLock(url),   # one in-flight turn per conversation
)
await worker.serve()
```

Built-ins: `InProcessQueue` / `InProcessEventSink` (dev, zero infra) and Redis adapters (prod).
Per-session locking and backpressure are part of the `AgentWorker` contract. This generalizes the
arq + Redis pub/sub + session-lock pattern the Mezon worker already runs.

---

## Design principles

- **PreAct, not free ReAct** — acting is pre-structured by lobes/stages/flows; ReAct is the inner
  loop of an `agentic` stage. [`preact.md`](./preact.md).
- **Deterministic core, pluggable edges** — recognition/activation/attention/flow resolution are
  pure and free; everything else (LLM, tools, embed, stores, queue) is a protocol with an
  in-memory default. The same agent runs in a unit test, in-process, or behind a Redis pool by
  swapping adapters.
- **Uniform `Activable`** — lobes, stages, flows, and skills share
  `(id, name, description, use_when, signal)` and activate by the same free, deterministic rule.
- **Rows, not branches** — extend by registering a lobe/stage/flow/skill, never by forking the
  engine.
- **Inspectable by construction** — every routing decision is a free, explainable snapshot.
- **Portable** — the spec is data; the core is a documented pure algorithm.

---

## Roadmap

The façade + kernel are built in phases (see the plan); each gates on the engine test suite,
the SDK-isolation test, kernel↔interpreter parity, and the free benchmark ladder staying green.

1. Engine kernel (generic turn driver, seams, events, `Trace`)
2. First-class `Stage` + `StageRegistry`, the `Activable` interface, flow-by-reference
3. `PreactAgent` + builders + `@tool` + `Memory` + `Metacognition` + plugin system
4. Session/Memory stores + built-in plugins (`PluginWorkspace`, `PluginMCP`, `PluginOTel`)
5. LLM client classes
6. Serving (queue + pub-sub worker)
7. Probe/inspect/bench public API
8. Serializable spec + porting guide
9. Migrate `BotPolicyInterpreter` to delegate to the kernel

See also: [`preact.md`](./preact.md) · [`building-a-harness.md`](./building-a-harness.md) ·
[`contracts.md`](./contracts.md) · [`porting.md`](./porting.md).
