# Concepts — a guided reading order

> The deep dives behind PreAct. Start with [`../preact.md`](../preact.md) (the model in one page) and
> [`../api.md`](../api.md) (the public surface); the docs here open each idea up, **numbered in the
> order they build on each other**.

Read top to bottom for the full mental model, or jump to a group. Each file is self-contained and
cross-links its neighbours; the number is just the suggested path through them. Docs marked
**(direction)** describe a designed-but-not-fully-shipped capability — each carries a status banner.

## 1 · The model

| # | Doc | What it covers |
|---|---|---|
| 01 | [Architecture](./01-architecture.md) | The full picture — the OX/OY plane, the lobe & flow axes, metacognition, and the per-turn execution pipeline. |

## 2 · The flow axis (OX) — how a turn progresses

| # | Doc | What it covers |
|---|---|---|
| 02 | [Intent & Paths](./02-intent-and-paths.md) | How a turn's recognized intent biases the lobes and selects the flow. |
| 03 | [Reply Flow](./03-reply-flow.md) | Collectors → the one terminal response stage that renders the message. |

## 3 · Context & memory (OY) — what's in the window

| # | Doc | What it covers |
|---|---|---|
| 04 | [ReAct Context Management](./04-react-context-management.md) | The core machinery: CDS scoring, the 3-tier router, the per-hop funnel, compaction. |
| 05 | [Tool Use at Scale](./05-tool-use-at-scale.md) | The tool-result lifecycle (ingest → demote → digest → offload → read-back) that keeps the prompt bounded. |
| 06 | [Universal Memory](./06-universal-memory.md) | One entry model over every kind of information; two tiers (flash / long-term); the scoped durable memory (`conversation`/`channel`/`user`/`bot`); value-budgeted selection. |
| 07 | [Shared Context](./07-shared-context.md) **(direction)** | One handle every component reads/writes, spanning `turn` → `bot` scope. |

## 4 · Capabilities, tools & control

| # | Doc | What it covers |
|---|---|---|
| 08 | [Reasoning as a Tool](./08-reasoning-as-a-tool.md) | The mental model: tools are how the agent reshapes its own thinking — the foundation under skills + metacognition. |
| 09 | [Skills](./09-skills.md) | Progressive-disclosure SOPs the agent activates on demand (the *capability* family of reasoning tool). |
| 10 | [Plugins](./10-plugins.md) | The single composable extension mechanism: a module that contributes lobes / stages / flows / skills / tools. |
| 11 | [Metacognition](./11-metacognition.md) **(direction)** | Thinking about thinking — reframed as a *capacity module* you equip on any agent or subagent. |
| 12 | [Subagent Fan-out](./12-subagent-fanout.md) **(direction)** | Delegating a slice of a turn to scoped sub-thinkers that return memos, not dumps. |
| 13 | [Task Execution Mode](./13-task-execution-mode.md) **(direction)** | Long-rail work — a checklist/rail driven over many steps via the `map` loop. |

## Assets & notes

- `*.svg` (`overview`, `the-model`, `turn-pipeline`, `core-and-extensions`) are rendered diagrams, not
  reading material.
- **Legacy naming:** `01-architecture.md` is ported from the in-tree engine this SDK was extracted from
  and still says *agent-core* / `agent_core` in places. The model maps over one-to-one; for current
  package names trust [`../../README.md`](../../README.md) +
  [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) (the package is `agent_sdk` / **PreAct**).
- When you add a concept doc, give it the next number, drop it in the right group above, and add the
  reciprocal cross-links in its `Related` section.
