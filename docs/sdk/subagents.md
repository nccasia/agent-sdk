# Subagents & delegation

> A subagent explores in its own space and hands back a **memo** — never its raw working set.
> Delegation runs N of them when a task pays off; fan-in keeps the conclusions, not the dumps.

This is the practical guide: **how subagent fan-out works**, **how it is implemented**, and **how to
use it**. For the conceptual model and the orchestrator-worker rationale, read
[`concepts/12-subagent-fanout.md`](../concepts/12-subagent-fanout.md); for the public API one-liners,
[`api.md` → Subagents](../api.md). This page ties them together.

---

## Contents
- [What a subagent is](#what-a-subagent-is)
- [How it works](#how-it-works)
  - [The fan-out loop (`loop="map"`)](#1-the-fan-out-loop-loopmap)
  - [Two execution shapes](#2-two-execution-shapes-sequential--parallel)
  - [Per-worker context isolation](#3-per-worker-context-isolation)
  - [Bounded failure](#4-bounded-failure)
  - [Delegation: the decision + the call](#5-delegation-the-decision--the-call)
- [How it is implemented](#how-it-is-implemented)
- [Usage](#usage)
  - [A. Define subagents](#a-define-subagents-in-code-or-files)
  - [B. Mount the plugin & delegate by name](#b-mount-the-plugin--delegate-by-name)
  - [C. Auto-delegation on complex queries](#c-auto-delegation-on-complex-queries)
  - [D. Fan out a stage directly (no plugin)](#d-fan-out-a-stage-directly-no-plugin)
  - [E. Read the results](#e-read-the-results)
- [Configuration reference](#configuration-reference)
- [Invariants & guarantees](#invariants--guarantees)
- [Tuning when delegation is wrong](#tuning-when-delegation-is-wrong)
- [Benchmark](#benchmark)
- [Limitations (deferred)](#limitations-deferred)

---

## What a subagent is

A **subagent** is a scoped, bounded sub-execution defined by `(prompt, tools, lobes, model, budget)`
that returns a **compressed result**, never its raw context. It does a slice of the work in a
*separate* space, with its *own* prompt / tools / model / budget, and the parent keeps only the
conclusion. Isolation is the primitive that makes specialization and parallelism safe — no worker can
flood another's (or the parent's) context.

The SDK realizes this **on top of the engine's generic `map` loop** — a subagent is the *named,
reusable* form of the work-item that loop already runs. There is no second interpreter: a subagent is
a registry row / item field, not a kernel branch.

This mirrors Claude Code's subagents: a declarative unit (`name`, `description`, `tools`, `model`,
`prompt`) you define once and invoke many — in code or as `.claude/agents/*.md` files.

---

## How it works

### 1. The fan-out loop (`loop="map"`)

A `Stage` with `loop="map"` and a `fanout_key` reads `scratchpad[fanout_key]` — a list a prior stage
or tool filled — and runs **one bounded `_agentic` sub-execution per item**. Each item is a dict that
may override the worker's `system_prompt` / `tools` / `lobes` / `model` / `max_tokens` / `hops`. The
results are written back to `scratchpad[fanout_key + "_results"]`. An empty list degrades to a single
agentic run (parity — the turn is never lost).

```txt
        ORCHESTRATOR                 WORKERS (fan-out)                FAN-IN
   plan ──decompose──▶ work-  ┌─▶ subagent[0] ──result──┐
   (a stage/tool       list   ├─▶ subagent[1] ──result──┤─▶ synthesize ─▶ cite ─▶ filter
    fills the list)           ├─▶ subagent[2] ──result──┤   (aggregate)   (pinned, never
                              └─▶ subagent[N] ──result──┘                  a worker's call)
                                      │                          ▲
                       raw evidence ──┘ confined to each worker  │ only results (memos)
                                        (when isolated) ─────────┘ cross the boundary
```

### 2. Two execution shapes (sequential / parallel)

The same concept has two shapes, selected by `Stage.fanout_parallel`:

| | **Sequential** (`fanout_parallel=False`, default) | **Parallel** (`fanout_parallel=True`) |
|---|---|---|
| Order | item *i* sees items `0..i-1` as notes (**state-carry**) | items run at once, semaphore-bounded by `fanout_max` |
| Use when | steps build on each other (a plan rail, a pipeline) | facets are independent (research, an audit, a comparison) |
| Wall-clock | sum of items | slowest item |
| Output | streamed live | buffered per worker, **flushed in submission order** (deterministic) |

The sequential shape is what the `tasks` plugin's TodoRail relies on; the parallel shape is the
research lobe's `asyncio.gather` shape, generalized to any map stage.

### 3. Per-worker context isolation

By default every worker shares the turn's evidence channel (`retrieved_chunks` / `already_read`).
With `Stage.fanout_isolated=True`, **each worker gets a fresh evidence pool** — worker A's retrieved
chunks never enter worker B's window, and only the worker's result crosses back. This is the input-side
boundary that turns an in-turn fan-out into a real Claude-Code subagent (fresh window + summary-only
return). The output-side boundary is already structural: the `Blackboard` rejects raw chunk kinds, so
raw chunks can never join the shared pool.

### 4. Bounded failure

Either shape is bounded-failure: a worker that raises — or exceeds an optional per-item `timeout`
(seconds) — is recorded `status="failed"` instead of being dropped or sinking the turn. The good
workers' results survive; the aggregate stage proceeds.

### 5. Delegation: the decision + the call

"Delegation" is the agent *choosing* to fan out. It has two halves, both deterministic where it
matters:

- **The decision (routing).** Which flow runs is signal-driven, never an LLM judging the pipeline.
  The `meta` flow (reflect → fan-out → synthesize) is recognized by a free signal: explicit cues
  ("step back", "rethink") *or*, when **auto-delegation** is on, a **complexity heuristic**
  (`complexity_score`) that fires on genuinely multi-faceted, decomposable queries and stays silent
  on single-fact ones.
- **The call.** Inside the `meta_reflect` step the model calls the `meta_control` tool with
  `action=fan_out` and a list of items. Each item may name a registered subagent
  (`{"agent": "reviewer", "input": "…"}`); a **deterministic enactor** resolves the name against the
  registry and writes the work-list. The `meta_fanout` stage then runs it parallel + isolated. This is
  Claude Code's "manual invocation by name" — the model *names*, the enactor *resolves*.

---

## How it is implemented

Everything sits **above** the kernel — `Subagent.to_item()` emits exactly the dict `Engine._map_stage`
already consumes.

| Piece | Where | Role |
|---|---|---|
| `Subagent` | `agent_sdk/subagents/definition.py` | The typed, named work-item. `to_item(input, label)` projects to the map dict; `from_row(dict)` builds from a row/frontmatter. |
| `SubagentRegistry` | `agent_sdk/subagents/registry.py` | `register` / `add_row` / `from_rows` / `get` / `all` / `names`; `resolve_item(item)` expands a named item (unknown → `KeyError`); `render_catalog()` lists `name — description`. |
| File loader | `agent_sdk/subagents/loader.py` | `load_agents_dir(path)` + `parse_agent_markdown(text)` read `.claude/agents/*.md` (frontmatter + body), reusing `skills/parser.py:split_frontmatter`. |
| Fan-out flags | `agent_sdk/stages.py` (+ `flows/flow.py`) | `Stage.fanout_parallel` / `fanout_max` / `fanout_isolated` (defaults reproduce today's behavior). |
| The loop | `agent_sdk/engine.py` | `_map_stage` dispatches sequential vs `_map_parallel` (gather + semaphore + buffered ordered flush); `_map_item_pool` returns a fresh pool when isolated; results land in `scratchpad[fanout_key + "_results"]`. |
| Named delegation | `agent_sdk/plugins/metacognition/tool.py` | `MetaControlToolRuntime(registry=…)`; `_fan_out` resolves `item["agent"]` via the registry before writing `scratchpad["meta_fanout"]`. |
| Delegation stages | `agent_sdk/plugins/metacognition/stages.py` | `meta_reflect` (lists the optional `subagent_catalog` lobe) → `meta_fanout` (`loop="map"`, parallel + isolated) → `synthesize`. |
| The decision signal | `agent_sdk/plugins/metacognition/path.py` | `recognize` (cues), `complexity_score(query)`, `make_recognize(auto_delegate=…)`. |
| Wiring plugins | `agent_sdk/plugins/subagents/__init__.py`, `…/lobes.py` | `SubagentsPlugin` builds the registry, installs the meta faculty wired to it, adds the `subagent_catalog` lobe. |

**The enriched result.** Each entry in `scratchpad[fanout_key + "_results"]` is
`{"label", "result", "status": "ok"|"failed", "tokens_used", "error"}` — the generalized "summary,
not dump" return (older readers that only used `label`/`result` are unaffected).

**The work-item dict** (what a map item — and thus a `Subagent` — can carry):

```python
{ "id": "...", "label": "...", "input": "...",   # identity + the worker's task
  "agent": "reviewer",                            # (delegation) resolve from the registry
  "system_prompt": "...", "tools": [...], "lobes": [...],   # its own prompt / restricted belt
  "model": "...", "max_tokens": 1024, "hops": 12, "timeout": 30 }   # its own budget + failure cap
```

---

## Usage

### A. Define subagents (in code or files)

```python
from agent_sdk import Subagent, SubagentRegistry, load_agents_dir

# in code …
registry = SubagentRegistry([
    Subagent(
        "reviewer",
        description="reviews code for bugs and security issues",   # WHEN to delegate
        instructions="You REVIEW code. Be specific; cite line numbers.",  # the worker's prompt
        tools=["read", "grep"],     # restricted allowlist (empty ⇒ inherit the stage's)
        model="claude-haiku-4-5",   # optional per-worker model tier
        hops=8,                      # optional per-worker budget
    ),
])
registry.add_row({"name": "tester", "description": "writes unit tests"})   # declarative row

# … or load from .claude/agents/*.md (Claude-Code-faithful)
registry = SubagentRegistry(load_agents_dir(".claude/agents"))
```

A `.claude/agents/reviewer.md` file:

```markdown
---
name: reviewer
description: reviews code for bugs and security issues
tools: read, grep
model: claude-haiku-4-5
hops: 8
---
You are a meticulous code reviewer. Be specific and cite line numbers.
```

(Frontmatter keys: `name` — defaults to the file stem; `description`; `tools` — comma list; `model`;
`max_tokens`; `hops`. The body is the worker's `instructions`.)

### B. Mount the plugin & delegate by name

`SubagentsPlugin` wires the registry into the metacognition `meta_control` enactor and adds the
`subagent_catalog` lobe so the reflect step *sees* the available subagents.

```python
from agent_sdk import PreactAgent
from agent_sdk.plugins.subagents import SubagentsPlugin

agent = PreactAgent(
    client=AnthropicClient("claude-opus-4-8"),
    instructions="…",
    plugins=[SubagentsPlugin(registry)],          # or SubagentsPlugin(agents_dir=".claude/agents")
)
```

The model delegates by naming a subagent inside its reflect step:

```jsonc
// what the model emits in the meta_reflect step:
meta_control(action="fan_out", items=[
  {"agent": "reviewer", "input": "review the auth module"},
  {"agent": "tester",   "input": "write tests for the auth module"},
])
```

The enactor resolves each name (unknown → a clear tool error, never a silent pass) and the
`meta_fanout` stage runs them parallel + context-isolated. `cite`/`filter` then ground the
**aggregated** results — a subagent can never decide to skip grounding.

### C. Auto-delegation on complex queries

`SubagentsPlugin` enables `auto_delegate` **by default** (delegation is its purpose): the agent
reflects-then-fans-out on genuinely multi-faceted queries *without* an explicit "step back" cue.

```python
SubagentsPlugin(registry)                       # auto_delegate=True (default)
SubagentsPlugin(registry, auto_delegate=False)  # cue-only delegation (conservative)
```

```python
# routes to the meta flow (decompose → fan out → synthesize):
"Compare the GDP, population, and land area of Canada, Australia, and Brazil."
# stays single-shot (no over-delegation):
"What is the capital of France?"
```

The decision is a pure function of the query (`complexity_score`) — deterministic, no LLM judging the
pipeline. The bare `MetacognitionPlugin` keeps `auto_delegate=False` (conservative) so a plain
metacognition install is unchanged.

### D. Fan out a stage directly (no plugin)

You don't need the metacognition plugin to use fan-out — set the flags on any `loop="map"` stage and
fill the work-list from a prior stage or tool:

```python
from agent_sdk import stage

stage(
    "research",
    lobes=["research", "synthesize"],
    loop="map",
    fanout_key="sub_questions",   # reads scratchpad["sub_questions"]
    fanout_parallel=True,          # run workers concurrently
    fanout_isolated=True,          # each worker gets a fresh evidence pool
    fanout_max=8,                  # concurrency / item cap (≤ 40)
    tools=["kb.retrieve", "kb.read_chunk"],
    hops=12,
)
```

A `Subagent` slots straight into such a work-list:

```python
item = registry.get("reviewer").to_item(input="review the auth module")
scratchpad.set("sub_questions", [item, {"label": "perf", "input": "profile the hot path"}])
```

### E. Read the results

After the stage runs, the per-worker results are in the scratchpad:

```python
results = scratchpad.as_list("sub_questions_results")
# [{"label": "reviewer", "result": "...", "status": "ok",   "tokens_used": 412, "error": None},
#  {"label": "perf",     "result": "",    "status": "failed","tokens_used": 0,   "error": "timeout"}]
```

The synthesize/cite/filter stages aggregate and ground these for you; you only read the scratchpad
directly when composing a custom flow.

---

## Configuration reference

**`Subagent(name, *, description="", instructions="", tools=(), lobes=(), model=None, max_tokens=None, hops=None)`**
— empty `tools`/`lobes` ⇒ inherit the fan-out stage's belt.

**`Stage` fan-out flags** (only meaningful with `loop="map"`):

| Flag | Default | Meaning |
|---|---|---|
| `fanout_key` | `""` | the scratchpad list key to fan out over (required for `map`) |
| `fanout_parallel` | `False` | `True` ⇒ concurrent workers (gather); `False` ⇒ sequential state-carry |
| `fanout_max` | `40` | concurrency / item cap (always clamped ≤ 40) |
| `fanout_isolated` | `False` | `True` ⇒ fresh evidence pool per worker (no cross-worker leakage) |

**Per-item override keys:** `id`, `label`, `input`, `agent` (registry name), `system_prompt`, `tools`,
`lobes`, `model`, `max_tokens`, `hops`, `timeout` (seconds; parallel only).

**`SubagentsPlugin(agents=None, *, rows=None, agents_dir=None, flow=True, auto_delegate=True)`** —
`agents` is a list of `Subagent` or a `SubagentRegistry`; `rows`/`agents_dir` add more; exposes
`.registry`.

**`MetacognitionPlugin(*, flow=True, subagents=None, auto_delegate=False)`** — pass a registry to
enable named delegation; `auto_delegate` adds the complexity signal.

---

## Invariants & guarantees

These are enforced by the test suite — a violation is a regression, not a trade-off:

- **No second interpreter.** Workers reuse `Engine._agentic` via a scoped `Stage`. New capability is a
  registry row / item field, never a kernel branch.
- **Citations are not a worker's call.** `cite`/`filter` are pinned stages that run on the
  *aggregated* results. A subagent can't skip grounding; ground-or-refuse is the flow's guarantee.
- **Routing is deterministic.** The flow is chosen by free signals (`complexity_score` / cues);
  name resolution is a dict lookup. No LLM judges the pipeline (invariant #4).
- **Default-network parity.** Every fan-out flag defaults to today's behavior; a bare
  `MetacognitionPlugin` is unchanged; a no-plugin agent is byte-identical to the default network.
- **Budgets are explicit.** ≤ 40 items per map, `fanout_max` semaphore on parallel fan-out, per-item
  `hops`/`max_tokens`/`timeout`. Fan-out widens work, never the safety envelope.

---

## Tuning when delegation is wrong

The delegation behavior is tuned at the smallest surface, by symptom:

| Symptom | Likely cause | Lever |
|---|---|---|
| Under-delegates (misses complex queries) | `complexity_score` too strict | broaden the verbs/list/enum gates in `plugins/metacognition/path.py` |
| Over-delegates (fires on simple queries) | `complexity_score` too loose | tighten those gates |
| Decided right but didn't fan out | the reflect step didn't call `fan_out` | the reflect prompt (`stages.py`); sharpen subagent `description`s in the `subagent_catalog` |
| A facet is dropped from the answer | synthesize missed a worker's result | the `synthesize` lobe / aggregate prompt; the `meta_fanout` lobe slice |
| Cross-worker leakage / a slow worker stalls the turn | isolation/failure not engaged | set `fanout_isolated=True`; add a per-item `timeout` |

---

## Benchmark

[`benchmarks/delegationbench/`](../../benchmarks/delegationbench/) is the arbiter. It runs rich,
realistic, multi-faceted queries and grades both halves:

- **free (deterministic):** the **decision** — `complexity_score` precision/recall over a labeled
  dataset — plus the fan-out engine invariants (isolation, ordering determinism, bounded failure)
  under the `FakeClient`. Joins the free CI gate.
- **live:** the **execution** — real delegation precision/recall (did it call `fan_out` when it
  should?) and fan-in fidelity (did every facet land in the answer?).

```bash
python benchmarks/delegationbench/run.py                              # free tier
python benchmarks/delegationbench/run.py --live --report --label base # + live behavior
```

---

## Limitations (deferred)

Documented in [`concepts/12-subagent-fanout.md`](../concepts/12-subagent-fanout.md) under
*Implementation status*:

- **Nested maps** — a worker that itself fans out.
- **Recursive `PreactAgent` subagents** — a real child run, not a scoped stage (Claude Code's
  "teams"/"workflows" tier).
- **Automatic delegation by an LLM classifier** — the SDK delegates by deterministic signal + named
  call; it does not let a model freely choose the pipeline shape (invariant #4).
- **Inter-worker messaging** — workers report to the orchestrator; they don't talk to each other.
