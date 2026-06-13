# PreAct SDK — documentation

> Everything to understand, use, and extend the SDK. Each surface below points deeper; read top to
> bottom for the full picture, or jump to the one you need.

## Start here

1. **[`preact.md`](./preact.md)** — the model in one page: what *pre-structured acting* is, the OX/OY
   axes (lobes × stages/flows), and metacognition. Read this first.
2. **[`api.md`](./api.md)** — the public surface: `PreactAgent`, the building blocks
   (lobes / stages / flows / skills / tools / plugins), sessions & memory, clients, inspection, serving.

## The surfaces

| Surface | Doc | What it is |
|---|---|---|
| The model | [`preact.md`](./preact.md) | The one-page mental model — start here. |
| Public API | [`api.md`](./api.md) | The complete public API reference. |
| Contracts | [`contracts.md`](./contracts.md) | The per-turn data contracts / protocols (`LlmCall`, `TurnContext`, `ToolRuntime`, memo models). |
| Concepts | [`concepts/`](./concepts/README.md) | The numbered deep-dives — the *why & how* behind each idea. See the [concepts README](./concepts/README.md). |
| Build a harness | [`building-a-harness.md`](./building-a-harness.md) | Wiring the SDK into an app, with benches & evals. |
| Porting | [`porting.md`](./porting.md) | Reimplementing PreAct in another language (Rust / Go / JS / …). |

## Concepts at a glance

The [`concepts/`](./concepts/README.md) deep-dives are numbered in reading order and grouped:

1. **The model** — `01-architecture` (the OX/OY plane + metacognition + execution).
2. **The flow axis (OX)** — `02-intent-and-paths`, `03-reply-flow`.
3. **Context & memory (OY)** — `04-react-context-management`, `05-tool-use-at-scale`,
   `06-universal-memory`, `07-shared-context`.
4. **Capabilities, tools & control** — `08-reasoning-as-a-tool`, `09-skills`, `10-plugins`,
   `11-metacognition`, `12-subagent-fanout`, `13-task-execution-mode`.

Docs marked **(direction)** in the concepts README describe a *designed-but-not-shipped* capability —
each carries a status banner. Everything else documents shipped behavior.

## Notes

- For current package names trust [`../README.md`](../README.md) + [`../CONTRIBUTING.md`](../CONTRIBUTING.md)
  (the package is `agent_sdk` / **PreAct**); a few ported concept docs still say *agent-core* in places.
- `concepts/*.svg` are rendered diagrams, not reading material.
