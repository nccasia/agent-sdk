# Method — toolbench

The optimization approach for the SDK's **tool-use** machinery. Standard: `../_shared/TEMPLATE.md`.
Ported from the monorepo `benchmarks/toolbench` (which tested `tool_strategy` trimming on the shipped
assistant), re-authored on the agent-sdk public surface and made leaf-pure.

## What it certifies
That a PreAct agent can **define, expose, route, and run tools at scale** correctly: `@tool` spec
generation, the `FunctionToolRuntime`/`CompositeToolRuntime` boundary, embedded `MCPToolRuntime`
discovery, the `ToolSelectLobe` adaptive-exposure algorithm, and the live agentic `tool_loop`.

## The lever (optimization approach)
When toolbench is NOT_READY, the fix is tuned **here** (surfaces in
`../../.claude/skills/preact-bench/reference/optimization-surfaces.md`), smallest blast radius first:

| Failing dimension | Root-cause signal (probe / trace) | Lever (surface to tune) |
|---|---|---|
| `select.*` (essential dropped / irrelevant kept / recall-precision under flood) | wrong tools survive the trim | `tool_select` weights (`w_l1`/`w_l2`, `min_activation`, `max_tools`) + the `essential(name)` predicate (`agent_sdk/tools/lobes/tool_select.py`, `network/context_builder.py:score_relevance`) |
| `loop.bounded` / `loop.terminated` (loop runs away or never answers) | too many hops / no final answer | the agentic stage's `loop` config, `tool_loop` `max_loops` + `drop_tools_on_final_hop` (`agent_sdk/lobes/runtime.py`) |
| obs-tail overrun (diagnostic: `hops`, tokens) | observations flood the window | the tool-use-at-scale funnel budgets (`working_set_budget`, `keep_last_full`) / the `retier` callback |
| `spec.*` (malformed schema / bad required inference) | `@tool` mis-maps a signature | the `@tool` schema generation (`agent_sdk/tools/__init__.py`) — a real bug, fix + regression test |
| `composite.*` (routing / external-marking) | dispatch or never-drop guard wrong | `CompositeToolRuntime` (`agent_sdk/contracts/tools.py`) / `MCPToolRuntime` (`agent_sdk/mcp.py`) |

Hypothesis space (a wave may change): the tool-use surfaces above + their weights. Out of scope:
the gating dataset, the interpreter, anything that weakens a gate.

## Metrics & gates
| Metric / check | Tier | Direction | Gate | Gating? |
|---|---|---|---|---|
| `spec.wellformed`, `spec.required_inference`, `spec.pydantic_schema`, `spec.requires_captured`, `spec.invoke_stringifies` | free | — | pass | gate |
| `select.essentials_kept`, `select.relevant_kept`, `select.irrelevant_dropped`, `select.budget_respected`, `select.parity_dark` | free | — | pass | gate |
| `composite.mcp_discovered`, `composite.mcp_called`, `composite.routing`, `composite.external_marked` | free | — | pass | gate |
| `loop.tool_called`, `loop.terminated`, `loop.bounded` | live | — | pass | gate |
| `tools_dropped`, `hops` | both | — | — | diagnostic (non-gating) |

## Tiers & dataset
- **free** (no provider): `spec`, `select`, `composite` (+ embedded MCP). Deterministic — these run in
  every invocation and gate without creds.
- **live** (`--live`, real provider): `loop` — a real agentic `tool_loop` over `@tool`s.
- Dataset: `dataset/scenarios.jsonl` holds the live lookup queries (the free modes are unit-style and
  inline). Future live mode `scale` (adaptive exposure under flood with `budgets={"tool_strategy":
  "adaptive"}`) is deferred — the adaptive concept is gated deterministically in `select` today.

## READY means
All free gating checks pass, and (when `--live`) the `loop` checks pass. Exits 0 iff READY.
