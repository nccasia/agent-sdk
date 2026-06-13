# PreAct — pre-structured acting

**PreAct** is the name for this engine's reasoning model. It is *augmented Act*:
the agent does **not** free-act by letting the LLM choose tool calls turn by turn
(vanilla ReAct). Instead its acting is **pre-structured by a well-known thinking
model** — a deliberate cognitive pipeline the engine resolves *before and around*
the model's action.

> **Pre** = the thinking structure precedes and shapes the act.
> The LLM still reasons and acts — but inside a scaffold the engine pre-computes,
> not free-form.

## ReAct vs PreAct

| | ReAct (vanilla) | PreAct (this engine) |
|---|---|---|
| Who structures the action? | the LLM, ad hoc each step | a deliberate cognitive model, resolved deterministically |
| What fires into context? | whatever the loop accumulates | the activated lobe subgraph (free, deterministic) |
| How does a turn progress? | model decides next tool/answer | a named **flow** of steps, selected by recognized intent |
| Guardrails | prompt-level | pinned output-contract lobes (`cite`/`filter`), metacognition |
| The role of tool-calling | the whole loop | one mode of one step (`loop="agentic"`) |

PreAct does not throw ReAct away — it **wraps** it. When a flow step needs tools,
its inner loop is still genuine ReAct (reason → act → observe), here with the
[funnel](../react/funnel.py) tiering observations so context narrows toward the
answer. PreAct governs the *macro* (which steps run, what context they see);
ReAct is the *micro* loop inside an agentic step.

## The well-known thinking model PreAct acts on

The lobe network is brain-shaped — a layered cognitive pipeline (RFC 0015):

```
B0 instinct    reflex gates (golden / refusal before, answer-guard after)   ← core, not lobes
B1 perception  free, deterministic feature extraction                        ← core, not lobes
B2 memory      recall lobes enrich the blackboard
B3 skill       procedure selection
B4 cognition   the work: classify · plan · research · synthesize
B5 expression  output contract: cite (pinned) · filter (pinned) · format
```

Two orthogonal axes ride on it:

- **OY — lobe axis (context):** *what* fires. A pure activation function over the
  layers decides the per-turn subgraph — never an LLM judging the pipeline.
- **OX — flow axis (time):** *how* action progresses. The recognized intent
  (path) selects a named flow; each step consults a slice of lobes and runs
  `none` / `single` / `agentic`.

A **metacognition** layer monitors the object-level state and regulates the next
step (trim a slice, retry, skip) within an allow-list — `cite`/`filter` are
pinned and never skippable.

That layered model + deterministic activation + named flows is the "well-known
thinking model" PreAct acts on: the act is *prepared* by structured reasoning,
not improvised by the tool loop.

## In the SDK

Everything PreAct needs to run is in this `sdk/` leaf — contracts, the activation
network, the `Lobe`/`Flow` frameworks + registries, `tool_loop`, metacognition,
the funnel. The concrete lobes/flows that *fill in* the thinking model are the
project's (bring your own). See [`building-a-harness.md`](./building-a-harness.md)
to assemble a PreAct agent, and [`../../../docs/architecture.md`](../../../docs/architecture.md)
for the full two-axis treatment.
