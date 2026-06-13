# Metacognition — a capacity you equip, not a supervisor you bake in

> Two kinds of thinking ride in one turn: **agent thinking** does the task; **meta thinking** decides
> *how* the task is approached. Metacognition is the module that gives an agent — or a subagent — the
> **lobe, stage, and tool** to think about its own thinking and reshape it.

> **Status: direction.** Today's metacognition is the deterministic kernel supervisor; the capacity
> module described here is designed-but-not-shipped. See *Implementation status* below.

## Meta thinking vs agent thinking

A turn runs two levels of thought at once:

- **Agent thinking (object-level).** *Doing the task* — the base lobes fire context, stages run, tools
  execute, the answer is synthesized and grounded. This is the work itself.
- **Meta thinking (meta-level).** *Reasoning about the work* — which skills fit this turn, which flow to
  take, whether to fan out subagents, whether to trim/retry/skip a step. This is reasoning about *how*
  the object level should proceed.

The codebase already names this split as **object-level** vs the **meta level** (`architecture.md`:
"Metacognition is the meta level. It does not replace the object-level work"). This doc keeps that
vocabulary and answers a different question: *where does the meta level live, and how does an agent get
it?*

## The reframe: metacognition as a module

Today metacognition is **baked into the kernel**: a `Metacognition` controller passed to the engine
(`agent.py:160`, `engine.py:392`), always on, that runs a deterministic `monitor → regulate` at each
step (`engine.py:836`). It **supervises** the pipeline — but it contributes **nothing to the agent's
surface**: no lobe, no stage, no tool. It watches; it does not *equip*.

The reframe: make metacognition a **module — like a skill or a tool**. In the SDK that means a
**plugin** (`plugins.md`): a first-class component that, at assembly, contributes a *capacity surface*
to the agent. A metacognition plugin contributes a **lobe + stage + tool**, so the agent gains the
*ability* to think about thinking — exactly the way the `tasks` plugin contributes a lobe + stages +
flow + tool to give an agent the ability to run a checklist (`plugins/tasks/__init__.py`), or
`support_triage` contributes a full surface (`plugins/support_triage/`).

```python
from agent_sdk import PreactAgent
from agent_sdk.plugins import MetacognitionPlugin   # the module this doc proposes

agent = PreactAgent(client=…, plugins=[MetacognitionPlugin()])   # now the agent can think about thinking
```

Metacognition stops being a layer welded above every agent and becomes a **faculty you grant** —
present when you add it, gone when you don't, scopable to whom you choose.

## What the module contributes

The same three-part surface every capacity module uses (`AgentSetup.add_lobe/add_stage/add_tool`,
`plugins/base.py`):

```txt
                    ┌──────────── MetacognitionPlugin.install(setup) ────────────┐
   OY  add_lobe  ──▶│ meta-context lobe : surfaces "how you are thinking" —       │
                    │   the path, the flow, active skills, the trajectory so far  │
   OX  add_stage ──▶│ meta step         : a reflect/regulate step in the flow     │
       add_tool  ──▶│ meta-control tool : the agent CALLS it to reshape thinking  │
                    └────────────────────────────────────────────────────────────┘
                                              │ writes a meta-decision
                                              ▼
              deterministic enactors read it ──▶ skills · flow · subagents · trim/retry/skip
```

- **The lobe (meta context).** Renders the agent's own thinking state into the prompt — the recognized
  path, the selected flow, the skills in use, the observations so far. The agent can't reason about its
  approach if it can't *see* it; this lobe is that mirror. (Today nothing surfaces this; the inspection
  snapshot exists but never enters the prompt.)
- **The stage (meta step).** A reflect/regulate step the flow can include — a deliberate point where
  meta thinking runs, distinct from the object-level work steps.
- **The tool (meta control).** The agent *calls* it to reshape its thinking: pick skills, switch flow,
  control subagents, trim/retry/skip. The tool **writes** a meta-decision; a deterministic enactor
  **reads** and applies it — the *reason → write → enact* pattern already live for skills
  (`skill_strategy="reason"` writes `lobe_outputs["skills_in_use"]`, the `skill_active` lobe drives it —
  `skills/runtime.py:116`, `skills/prompt.py:66`).

## The levers — what meta control reshapes

| Lever | Agent-thinking default (deterministic) | Reshaped by the meta tool via | Enactor (deterministic) |
|---|---|---|---|
| **Skills** | static / relevance-ranked activation | the tool writes the chosen slugs | `skill_active` / `skill_select` lobes |
| **Flow / path** | signal recognizers (`recognize_paths`) | the tool writes a path/flow preference | the path resolver (reads it as a signal) |
| **Subagent control** | the flow decides fan-out; `_map_stage` runs it | the tool writes the work-list / per-item specs | `_map_stage` ([Subagent Fan-out](./12-subagent-fanout.md)) |
| **Trim / retry / skip** | deterministic `regulate` (today) | the tool requests the action | the engine's regulation seam (`engine.py:836`) |

Every lever follows the same shape: the **meta tool decides**, a **deterministic enactor applies** —
the object level never asks an LLM to judge it inline.

## Composable onto any agent — or any subagent

Because it's a module, metacognition composes wherever capacity composes. Add the plugin to an agent and
it can meta-think. The sharper property: a **subagent can be granted it too.** A fanned-out subagent is a
scoped sub-execution whose lobe and tool slice can be overridden per work-item
(`engine.py::_map_stage`, ~`:1356`: `lobes=list(item.get("lobes") or stage.lobes)`,
`tools=list(item.get("tools") or stage.tools)`). Hand a subagent the meta-context lobe and the
meta-control tool and it gains its own think-about-thinking faculty — recursively: a subagent can
reshape *its* sub-thinking the way the parent reshapes the turn.

```txt
   parent agent  [+ metacognition]            ── reshapes the turn's skills/flow/fan-out
        └── fan-out ──▶ subagent A [+ metacognition]   ── reshapes its OWN sub-thinking
                        subagent B [  base only     ]   ── plain worker, no meta faculty
```

**Honest gap.** Per-subagent *plugin* scoping doesn't exist yet — today a subagent borrows the meta lobe
and tool from the **globally-installed** module via the `_map_stage` override; it can't `install` a
plugin only it sees. Real per-subagent capacity is a to-build (below).

## Determinism — the new reconciliation

The SDK's invariant 4 (`CLAUDE.md`) says intent recognition, activation, attention/budget, and **flow
resolution** are *pure functions of `(spec, context)` — never an LLM judging the pipeline*. LLM-reasoned
meta-control seems to collide with it. It does not — once metacognition is a module rather than a hidden
judge:

- The **object-level core stays a pure function of `(spec, context)`.** The meta tool does not *resolve*
  the pipeline; it **writes a decision into context**. The deterministic resolver then runs unchanged,
  reading that decision as one more signal. (This is exactly how skill activation already works.)
- So no LLM **silently** judges the pipeline. Judgment is **equipped** (a tool the agent holds),
  **explicit** (a tool call), **traced** (recorded like every meta decision today), and
  **deterministically enacted**.
- The always-on deterministic `monitor → regulate` remains the **floor** (`observe`/`apply`); the module
  adds LLM-reasoned reshaping *on top of* it, never underneath it.

The invariant is **refined, not broken**: *the object level is deterministic; meta judgment is a
declared capacity whose output is deterministically enacted.* That is the line between a maintainable
engine and an unpredictable one.

## Implementation status

This lands on machinery that already exists; the default agent is unchanged.

**Live (the substrate).**
- The plugin capacity surface — `Plugin` / `AgentSetup` (`add_lobe`/`add_stage`/`add_flow`/`add_tool`,
  `plugins/base.py`), `PluginRegistry` toggling (`plugins/registry.py`). Templates: `tasks` (lobe +
  stages + flow + tool) and `support_triage` (full surface).
- Today's **deterministic kernel metacognition** — `monitor → regulate`, `observe`/`apply` modes +
  precedence, pinned `cite`/`filter` (`metacognition_facade.py` `PINNED_UNSKIPPABLE`,
  `metacognition/controller.py` `_DEFAULT_APPLY_ACTIONS={adjust_lobe_slice}`).
- The **reason → write → enact** pattern — already live for skills (`skill_strategy="reason"` →
  `lobe_outputs["skills_in_use"]` → `skill_active`).

**To build.**
- Package metacognition **as a plugin** — the meta-context lobe, the meta step, the meta-control tool.
- The meta-control tool's **enactors** for skills / flow / subagent (skills already have theirs).
- **Per-subagent capacity scoping** — let a subagent receive the module, not just borrow its lobe/tool.
- **Feed the controller what it sees** — the deterministic decision at `engine.py:836` is passed only
  `target_flow`/`target_step`; the lobe/flow/engine snapshots are `None`, so today's monitor runs nearly
  blind. Wire `adjust_lobe_slice` (infra is ready, unapplied).

**Deferred.**
- Recursive meta plugins (a meta module that reasons about the meta module).
- Meta choosing arbitrary *new* flows, or rewriting the pipeline wholesale.

## Boundaries

- **Object-level resolution stays deterministic** (refined invariant 4) — the meta tool writes context;
  it never becomes the resolver.
- **`cite` / `filter` are pinned** (`SafetyPlugin`) and are **never** a meta decision — ground-or-refuse
  is not reshapeable (`PINNED_UNSKIPPABLE`).
- **Opt-in and traced** — metacognition is a plugin you add; every meta decision is recorded, as today.
- **Budgeted** — meta thinking runs at decision points (a stage, a tool call), not on every hop.
- **No second interpreter** — the module reuses the `AgentSetup` seam for capacity and the existing
  deterministic `MetaController` for the floor; it does not fork the engine.

## Benchmarking

Live-only, per repo convention. The natural gate is **with-vs-without the metacognition module**: does
equipping it produce better skill / flow / fan-out choices than the deterministic default? — the same
shape as today's `apply`-vs-`observe` A/B (does applying regulation beat just watching). Metrics:
decision quality (did meta pick the better approach), task accuracy uplift, and cost (meta tokens spent
per turn vs the gain).

## Related

- [Architecture](./01-architecture.md) — the Metacognition section this deepens (monitor/regulate,
  observe/apply) and the OX/OY axes the module plugs into.
- [Plugins](./10-plugins.md) — the capacity-surface model (`Plugin`/`AgentSetup`) the module is built on.
- [Subagent Fan-out](./12-subagent-fanout.md) — subagent control (a meta lever) and composing capacity onto
  a subagent.
- [Skills](./09-skills.md) — skill activation + the live *reason → write → enact* pattern the meta tool
  generalizes.
- [Reasoning as a Tool](./08-reasoning-as-a-tool.md) — the mental model under both skills and metacognition:
  the meta-control tool is a *reasoning tool* (the process family); `ActivateSkill` is the capability family.
- [Intent and Paths](./02-intent-and-paths.md) — flow/path selection the meta tool can bias.
- [PreAct](../preact.md) — the model: "metacognition supervising both axes."

## Design principle

Metacognition is not a watcher bolted above the agent. It is a **faculty you grant** — the lobe to see
its own thinking, the stage to reflect, the tool to change course. Bake nothing into the kernel that you
can hand to an agent as a capacity; then any agent, and any subagent, you give it to can think about how
it thinks.
