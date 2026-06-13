# agent-core Architecture

`agent-core` is the reusable assistant engine. It owns the object-level thinking
loop, context selection, tool use, final answer shaping, and the meta layer that
can observe and regulate those steps. The worker app is only the deployment
shell; engine behavior should live here.

The current architecture has two independent optimization axes. Think of them as
a Cartesian plane: **OX is the time axis, OY is the context axis.**

- **OX, the flow axis (time):** organizes the progressive *flow of action and
  decision* the engine executes — the steps that unfold over the course of a
  turn (left → right in time).
- **OY, the lobe axis (context):** organizes *what context and prompt
  contributions* are stacked inside the context window at any point on the rail.

So there are two distinct things to optimize, one per axis:

- **better context focus → a better OY** (the lobe axis: which context fires);
- **better flow of action/decision → a better OX** (the flow axis: which steps
  run, in what order, with what tools).

The metacognition layer sits above both axes. It monitors the object-level
state, then decides what the engine should think about next.

> **Paths vs flows?** A *path* is the turn's recognized intent; it BIASES lobes
> (OY) and SELECTS the flow (OX). For how recognition, flows, and lobes relate —
> and why one intent's definition should live in one place — see
> [`intent-and-paths.md`](./intent-and-paths.md).

## Package layout: the `sdk/` leaf vs the project

`agent_core` is split into two layers along a hard, test-enforced seam:

- **`agent_core/sdk/` — the unopinionated engine** (the future standalone
  `agent_sdk` package). It is the framework: the per-turn data **contracts**
  (`contracts/` — `LlmCall`, `LobeServices`, `TurnContext`, memo models, the
  `ToolRuntime` protocol, the canonical `PINNED_LOBES`), the deterministic
  **activation engine** (`network/activation.py` + `context_builder.py`), the
  `Lobe`/`Flow`/`FlowStep` **base classes + registries + `tool_loop` runtime**
  (`lobes/`, `flows/`), the **metacognition** framework, the ReAct **funnel**,
  and generic utilities (caches, scratchpad, guards, skills, inspection). It
  ships **no concrete lobes and no concrete flows** — you bring your own. It is
  a **leaf**: it imports the stdlib, third-party deps, and other
  `agent_core.sdk` modules — never `rag_core` / `arag_core` / `ingest_core`, and
  never back into the project. `tests/test_sdk_isolation.py` fails CI if that
  ever breaks; `tests/test_pinned_lobes_parity.py` keeps the SDK's pinned set in
  sync with `rag_core.policy.schema`.

- **`agent_core/` (the project) — everything that *uses* the SDK.** Every
  concrete lobe (`lobes/cognition|expression|memory|skill|task|tools|paths`),
  the default network assembly (`lobes/network.py`, `lobes/weights.py`), the
  flow definitions + stages (`flows/definitions`-style `defaults.py`,
  `flows/stages/`), the concrete tool runtimes (`tool_runtime.py`), the project
  **adapters** (`adapters/storage/` golden+reader caches, `adapters/clients/`
  HTTP relays), and `BotPolicyInterpreter` that wires it all to a `BotPolicy`.
  Project code may import `rag_core`/`arag_core` freely.

The framework binds to its instances through **default-provider hooks** rather
than imports: the SDK registries (`LobeRegistry`/`FlowRegistry`) call
provider callables that `agent_core.lobes` / `agent_core.flows` register at
import time (`set_default_providers` / `set_default_flows`), so a no-arg
registry still resolves the built-in network without the SDK ever importing a
concrete lobe or flow.

Old top-level import paths (`agent_core.memo`, `agent_core.metacognition`,
`agent_core.lobes.runtime`, …) remain valid through a single lazy alias finder
in [`agent_core/_compat.py`](../agent_core/_compat.py) (a `MetaPathFinder`
installed from `agent_core/__init__.py`) — so the package tree stays clean (no
scattered shim files) and the extraction-to-`agent_sdk` step stays
near-mechanical. `agent_core/lobe_network.py` is the one physical exception (it
augments the activation namespace with the deprecated `Stage` aliases).

For the SDK's own overview, layout, and a build-a-harness walkthrough, see
[`../agent_core/sdk/README.md`](../agent_core/sdk/README.md) and
[`../agent_core/sdk/docs/`](../agent_core/sdk/docs/) (`building-a-harness.md`,
`contracts.md`).

## Mental Model

Think of one assistant turn as a point on an OX/OY surface — OX is time (the
flow), OY is the context stacked at that moment (the lobes):

```text
OY lobe axis (context in the window)
  ^
  |  memory
  |  task_state
  |  skill_activate     each step in time consults a vertical slice of lobes
  |  research
  |  synthesize
  +--------------------------------------------------> OX flow axis (time)
       plan -> research -> synthesize -> cite -> filter
```

The lobe axis (OY) is a vertical slice of possible thinking resources available
at a point in time. Lobes are passive units that expose state nodes, context
nodes, and prompt blocks.

The flow axis (OX) is the staged execution path over time. Flow steps decide
which lobe slice to consult, whether the step is a single LLM call or an agentic
tool loop, and which tools are available.

Metacognition is the meta level. It does not replace the object-level work. It
observes the lobe axis, flow axis, and engine snapshot, then regulates the next
move when the current trajectory looks weak.

## Main Modules

`agent_core.interpreter`

The orchestration layer. `BotPolicyInterpreter` resolves policy, builds the turn
context, chooses the path, runs the flow pipeline, records trace fields, and
returns the final envelope.

`agent_core.lobes`

The OY axis (context). Each lobe is an executable or prompt/context-producing
unit such as memory recall, skill activation, research, synthesize, cite,
filter, format, and task state. Lobe state machines decide which context nodes
activate for the current turn.

`agent_core.flows`

The OX axis (time/flow). A `Flow` is a named pipeline. A `FlowStep` is one stage
in that pipeline. The default flows currently cover `qna`, `research`,
`task_execute`, `clarify`, and `relational`.

`agent_core.flows.stages`

The concrete stage definitions. This folder keeps OX stages separate from OY
lobes. Stage modules define the step name, lobe slice, loop type, tools, and
standard context-window state nodes.

`agent_core.metacognition`

The meta-thinking layer. It monitors snapshots, produces observations, and
regulates the next action. It can continue, adjust a lobe slice, retry a step,
skip a step, or request meta review.

`agent_core.inspection`

Read-only snapshot helpers for lobe, flow, and engine state. These are the
main APIs for debugging, benchmarks, and future optimizers.

## OY: Lobe Axis (context)

A lobe is a small thinking unit. It can provide:

- state nodes: reactive gates that decide whether the lobe should activate
- context nodes: selected information for the prompt and blackboard
- prompt contributions: stable, slow, or volatile prompt blocks
- behavior: optional executable work through an injected LLM/tool seam

The important contract is separation of concerns. Lobes should not decide the
whole execution plan. They organize context and local behavior for a slice of
the turn.

Typical lobe examples:

- `memory_recall`: durable memory context (scoped `context_entries` — see
  [context-memory.md](./context-memory.md))
- `session_recall`: conversation/session context
- `task_state`: task state and task-related prompt blocks
- `skill_activate`: skill/tool guidance
- `research`: retrieval-oriented context
- `synthesize`: answer composition guidance
- `cite`, `filter`, `format`: expression and final-shaping concerns

`skill_activate` is the template for a lifecycle-shaped lobe: a skill is a
WORKFLOW the model is driven through, and the lobe's state machine emits one
context piece per lifecycle state — `skill:list` (the index),
`skill:select:cue` (selecting: skills declared, none loaded — pick one),
`skill.read:hint` (activating: call `skill.read`), `skill:in_use` (activated)
and `skill:guide` (driving: execute the remaining steps until done). The
runtime records the live state per skill in `trace.skills`
(`{slug: selecting|activated|driving}`) and reinforces the activated→driving
transition at the exact `skill.read` moment by appending the drive-forward
trailer to the tool output — mid-loop, on both run paths.

Per-bot lobe tuning uses flat weights, for example disabling or biasing lobe
state nodes. The flow axis has a separate `flow_` namespace so lobe tuning and
flow tuning do not leak into each other.

## OX: Flow Axis (time)

A flow is a complete named execution path. A flow step owns:

- the step name, such as `plan`, `research`, `synthesize`, `cite`, `filter`
- the lobe slice consulted by that step
- the loop mode: `none`, `single`, or `agentic`
- the tool allowlist for agentic steps
- flow-step state nodes, such as `context:tight` and `context:open`

Default flows:

| Flow | Steps | Purpose |
| --- | --- | --- |
| `qna` | `synthesize` | One-shot answer path. |
| `research` | `plan -> research -> synthesize -> cite -> filter` | Multi-step grounded research path. |
| `task_execute` | `advance -> format` | Advance a fired task's todo checklist, then deliver. |
| `clarify` | `synthesize` | Re-synthesize after referent resolution. |
| `relational` | `synthesize` | Greeting and social-register responses. |

Per-bot flow tuning uses keys such as:

- `flow_disable_<flow>`
- `flow_<flow>__step_<step>__disable`
- `flow_<flow>__step_<step>__lobe_<lobe_id>__add`
- `flow_<flow>__step_<step>__lobe_<lobe_id>__remove`

This lets us optimize OX progression without rewriting OY lobes.

## Metacognition

Metacognition is the engine's "thinking about thinking" layer. It has two jobs:

- monitor: observe what is happening in the object-level engine
- regulate: choose whether to continue or change the next thinking step

The public entry point is `MetaController`. The engine default is `apply` when
no policy or environment override is provided. **Metacognition is always on
(ENGINE 0.7.1) — there is no disabled mode.** Two modes exist:

- `observe` (the floor): monitor and trace every decision, never change
  execution
- `apply`: monitor, trace, and apply the allow-listed regulation actions

Configuration precedence:

1. `METACOGNITION` environment variable
2. policy `metacognition_mode`
3. legacy policy `metacognition_enabled` (False maps to `observe`)
4. engine default `apply`

`METACOGNITION=observe` is the emergency kill switch — it kills MUTATION, not
monitoring. Legacy off-tokens (`off`/`0`/`false`/`disabled`) map to `observe`
for back-compat: old configs degrade to shadow mode, never to blindness.

Policy can restrict active behavior with `metacognition_apply_actions`. By
default, active mode only applies `adjust_lobe_slice`, which trims optional
context lobes under pressure. More invasive actions such as `skip_step` and
`retry_step` require an explicit policy allowlist. `meta_review` is a traceable
regulation decision but is not a turn mutation in the interpreter.

Two hard limits sit under every mode (ENGINE 0.5.1):

- Pinned steps (`cite`, `filter`) are never skippable. The regulator escalates
  an empty pinned-step lobe slice to `meta_review`, and the interpreter's skip
  seam refuses pinned steps regardless of the decision — ground-or-refuse is
  never a metacognition decision.
- `adjust_lobe_slice` only trims the optional recall/skill lobes
  (`skill_activate`, `memory_recall`, `session_recall`, `ctxvar_resolve`);
  pinned lobes and step-defining lobes survive. The skill index itself lives in
  the base stage prompt (policy-driven), so trimming `skill_activate` removes
  its lobe-context extras, not skill visibility.

Current observations include:

- `context_tight`: the current flow step is under context-window pressure
- `empty_lobe_slice`: a flow step has no lobes to consult
- `empty_step_context`: a step executed without producing context nodes
- `low_confidence_path`: path recognition is emergent or low confidence

Current decisions include:

- `continue`: keep the object-level flow unchanged
- `adjust_lobe_slice`: trim optional lobes for the current step
- `retry_step`: rerun a step after missing context
- `skip_step`: avoid a step that cannot consult any lobe context
- `meta_review`: pause the object-level trajectory for higher-level review

The interpreter records meta decisions additively in trace payloads through
`meta` and `meta_queue`. The `meta` payload includes the mode and whether the
decision was applied. Disabled mode is a no-op and should not change legacy turn
behavior.

## Execution Flow

> **Rollout status (ENGINE 0.7.0).** The per-step pipeline below
> (`_run_pipeline`) **is the default runner**: `run()` executes the resolved
> path's flow steps through the same final contract (prefetch grounding,
> citation post-filter, ground-or-refuse, format pass, finalize); any gap —
> emergent path, empty result, error — DEGRADES to the legacy path, never
> losing the turn. Parity that made the flip safe: qna/clarify synthesize
> are AGENTIC over the full composed toolset (the legacy `simple_answer`
> contract), prefetch seeds the evidence channel, executed steps land in
> `stages_completed`. Rollback: `FLOW_EXECUTION=legacy` fleet-wide (env kill
> switch) or `flow_execution: "legacy"` per-bot. Note: pipeline prompt
> composition keys on FLOW STEP names and `simple_answer` has no flow-step
> counterpart — skills must also declare `synthesize` (enforced by
> skillbench's `stages.flow_axis_ready` lint rule; seed skills updated).

The pipeline turn follows this shape:

```text
1. Worker calls BotPolicyInterpreter with JobContext and query.
2. Interpreter loads policy, tools, skill registry, cache, and memory services.
3. Query is condensed and classified.
4. Path selection chooses a named flow, such as qna or research.
5. FlowRegistry customizes the flow for the bot's weights.
6. For each FlowStep:
   a. Build a TurnContext for this step.
   b. Inspect the flow axis and lobe axis.
   c. Build an EngineSnapshot from the current trace/blackboard.
   d. MetaController decides the next action when enabled.
   e. Apply turn-local regulation, such as trimming the lobe slice or skipping.
   f. Compose the system prompt from the step's active lobe slice.
   g. Run the step loop: none, single LLM call, or agentic tool loop.
   h. Record FlowStepResult and trace observability.
7. Final shaping applies citations, refusals, formatting, and safety helpers.
8. FinalEnvelope is returned to the worker shell.
```

The key design point is that each step sees a specific lobe slice. The same
lobe can be reused by multiple steps, and a new flow can be introduced without
rewriting lobe internals.

## Inspection And Snapshots

Use `agent_core.inspection` when you need to debug or optimize the engine:

- `inspect_lobe_axis(registry, ctx, weights)`: returns lobe activation,
  activated state nodes, context counts, and write metadata.
- `inspect_flow_axis(registry, path, weights, ctx)`: returns selected flow
  steps, disabled steps, lobe slices, tools, and flow-step state nodes.
- `snapshot_engine(trace, blackboard)`: returns path, flow, lobe, step,
  blackboard, and response state.
- `suggest_axis_optimizations(snapshot)`: returns pure optimization proposals.

Snapshots are read-only. They should be safe to collect in tests, traces,
benchmarks, and future tuning jobs.

## Optimization Surfaces

The engine is designed to be optimized at three levels:

- lobe unit: tune or inspect one OY lobe and its state nodes
- flow step: tune or inspect one OX stage and its lobe slice
- whole engine: snapshot path, flow, lobe activations, meta decisions, and final
  response quality

This is why lobe weights and flow weights are deliberately separate. It should
be possible to make a flow step narrower under context pressure without changing
the lobe's own state machine.

## Benchmarks

Package tests validate behavior. Benchmarks validate whether engine decisions
improve quality under realistic task pressure.

Useful benchmark entry points:

```bash
uv --directory packages/agent-core run pytest -q
uv --directory packages/agent-core run python ../../benchmarks/agentcore/run_agentcore_bench.py --suite free --label package-smoke
# metacognition gate — production default config only (apply mode, trim-only)
uv --directory packages/agent-core run python ../../benchmarks/corgictionbech/run_corgictionbech.py --label prodready
# flow axis (OX): sequencing, customization, handoff, ReAct loop, faults
uv --directory packages/agent-core run python ../../benchmarks/flowbench/run_flowbench.py --mode sequence --label dev
# skills: authoring lint + stage/context scoping (free), activation/uplift (LLM)
uv run python benchmarks/skillbench/run_skillbench.py --mode lint
uv run python benchmarks/skillbench/run_skillbench.py --mode scoping
```

`corgictionbech` runs the production default metacognition config only (no
off/on A/B — answers are decision-keyed fixtures, so an off baseline proves
nothing). Each scenario asserts the decision, the apply/observe channel, the
surfaced queue, the narrowed lobe slice, and the production-truth answer.
`flowbench` owns HOW the pipeline runs; `attentionbench` owns WHAT fires;
`skillbench` owns whether a skill's content is production-ready.

## The Flip (done — ENGINE 0.7.0) and the nightly follow-up

The default flipped to `"pipeline"` on direct validation of the new
architecture (owner decision: no legacy A/B). Evidence at flip time:

- the FULL agent-core suite (684 tests) green under
  `FLOW_EXECUTION=pipeline` — every legacy behavior lock-in (citations,
  condense threading, partial refusal, answer guards, golden gates) passes
  through the pipeline runner;
- flowbench's 7 free modes + the whole free ladder green under the new
  default; taskflow (LLM) grounded ≥ 0.9.

Standing follow-up (nightly tier — run these under the new default and
keep them green; all knobs are env-pinnable):

```bash
# the all-capability exam (gate runs use --trials 3, Wilson CI lower bound)
uv run python benchmarks/assistantbench/run_assistantbench.py --mode exam --trials 3 --label pipeline-default
# metacog APPLY contribution — observe is the floor (metacognition is
# always on); the A/B is apply vs observe, i.e. "does applying regulation
# beat just watching"
METACOGNITION=observe uv run python benchmarks/assistantbench/run_assistantbench.py --mode exam --trials 3 --label pipeline-observe
# grounding floors: faithfulness ≥ 0.85, citation accuracy ≥ 0.95, p95 ≤ 12s
uv run python benchmarks/funrag/run_funrag.py --mode e2e --label pipeline-default
```

Rollback at any point: `FLOW_EXECUTION=legacy` fleet-wide (env kill switch)
or `flow_execution: "legacy"` per-bot — the legacy path stays intact until
a full deprecation wave.

## Extension Rules

When adding a new lobe:

- keep it focused on one context or behavior concern
- expose state nodes when activation should be inspectable
- avoid embedding flow-order assumptions inside the lobe
- add tests for activation, prompt/context output, and behavior seams

When adding a new flow step:

- define it under `agent_core.flows.stages`
- declare the lobe slice explicitly
- choose the narrowest loop mode that works
- add context-window state nodes through the shared stage helper unless there is
  a reason to opt out
- add tests for default flow composition and customization weights

When adding metacognition behavior:

- monitor first, regulate second
- keep decisions explicit and traceable
- prefer turn-local adjustments before persistent optimization
- prove contribution with task-level accuracy, not only decision accuracy
