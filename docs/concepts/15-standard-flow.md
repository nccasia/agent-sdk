# The Standard Flow

> Most agents don't need a bespoke pipeline per intent. They need **one generic flow, graded by
> complexity** — built from a small vocabulary of reusable reasoning **states**
> (`understand → explore → plan → act → synthesize → ground → filter → respond`), each able to work on
> a **subject**, with **metacognition** shaping how many states run and in what order. Simple turns
> collapse to a cheap subset; complex problems expand the same states into a longer rail.

## The idea

A turn is a sequence of **states** (the OX flow axis, [01](./01-architecture.md)). Rather than a
hand-written pipeline per intent, the default is a small family of **complexity tiers** assembled from
one canonical state vocabulary. Two layers tune it independently:

- **Layer 2 — the reasoning building blocks (object level).** The canonical states, each a reusable
  `Stage` that takes an optional **subject** (the sub-question/aspect it works on). This is what the
  agent *does*.
- **Layer 1 — metacognition (thinking about thinking, [11](./11-metacognition.md)).** Watches the
  turn and shapes the rail: which states run, in what order, expanded over which subjects. This is the
  agent *deciding how to think*.

## The canonical states

| State | Loop | Role | Subject |
|---|---|---|---|
| `understand` | single / agentic | resolve referents, gauge complexity (may use tools to fetch context) | the raw turn |
| `explore` | agentic | scout the problem space before committing to a plan (optional) | the space |
| `plan` | single | decompose into sub-questions/aspects (optional, complex turns) | the goal |
| `act` | agentic / **map** | **the workhorse** — gather/compute/answer in a ReAct loop; **repeatable** (`act → act → act`, one run per subject) | a sub-question |
| `synthesize` | single | compose the answer from what was gathered | the memos |
| `ground` (`cite`) | single·t0 | attach citations — **pinned** | the draft |
| `filter` | single·t0 | ground-or-refuse — **pinned** | the grounded draft |
| `respond` | single | write the user-facing reply | the notes |

`ground`/`filter` are pinned ([PINNED_LOBES]): no weight, tier, or meta decision can strip them on a
grounding turn. The **subject** is a first-class `FlowStep.subject` (composed into the prompt as a
`<subject>` section), generalizing the per-map-item subject so *any* state — not just a fan-out item —
can be instantiated against one piece of the work.

## The complexity tiers (the static seed)

The default flows are these states grouped by how much machinery a turn needs. A path recognizes the
intent and the flow inherits its grounding (`production_flows` matches flow↔path by name):

- **direct** (`relational`) = `[respond]`-shaped — one cheap social reply, no tools, no grounding.
- **standard** (`qna`, `clarify`, `fallback`) = `[act]` (+ `understand` for a clarify follow-up) —
  one agentic loop over the full toolset. **The common case.**
- **deep** (`research`) = `[act, cite, filter]` — gather, then ground + ground-or-refuse.
- **steward** (`onboarding`) = admin-mode answer (no KB).

Cheap turns stay cheap; only `deep` pays for grounding. These seed shapes are what ships today.

## The mechanism: metacognition shapes the rail (Layer 1)

The seed shapes are static, but a complex problem isn't one-size. Metacognition reshapes the rail per
turn through the **reason → write → enact** seam ([08](./08-reasoning-as-a-tool.md)) — the model
*requests* via the `meta_control` tool; a deterministic enactor realizes it; pinned states always run:

```txt
   plan produces aspects ──▶ metacognition compiles a state plan
        scratchpad["state_plan"] = [{state:"act", subject:"aspect-1"},
                                    {state:"act", subject:"aspect-2"}, {state:"synthesize"}]
        │
        ▼ the movable cursor (_next_phase) enacts it, apply-gated + budgeted
   act(aspect-1) → act(aspect-2) → synthesize → cite → filter → respond
```

Today metacognition reshapes the *current* approach — `use_skills` / `bias_flow` / `regulate`
(trim/skip/retry) / `navigate` (redo/goto/done) — with the cursor already moving over a static stage
list. The **state-plan expansion** above (compile a plan into `act → act → act` over subjects) is the
next capability; it is **apply-gated and default-off**, so a no-plugin agent is byte-identical to the
seed tiers (parity).

## Boundaries

- **Pinned grounding is never reshapeable.** `cite`/`filter` run on every grounding turn; the cursor
  redirects any finish/jump to run an un-run pinned state.
- **Determinism in the core.** Routing, tier selection, gating, and the cursor are pure functions of
  `(spec, context)`; the model only *requests* a reshape (it never silently judges the pipeline).
- **One vocabulary.** New capability = a new state (registry row) or a tier composed from states —
  never a bespoke per-intent pipeline branch.

## Implementation status

**Live.** The canonical `act` workhorse state; the complexity tiers as the default flows
(`flows/defaults.py`); first-class `FlowStep.subject` threaded into the prompt; native path→flow
routing with inherited grounding; the metacognition supervisor (monitor→regulate→trim/skip/retry +
the `navigate` cursor) with the pinned guard.

**To build (Layer 1 expansion).** `MetaDecision.expand` + `plan_supervise` writing
`scratchpad["state_plan"]` + the cursor enacting it to expand `act` over subjects; the `understand`
and `explore` states as first-class agentic stages. All apply-gated/default-off.

**Deferred.** A learned policy for *when* to expand vs answer directly; cross-turn rails (task mode,
[13](./13-task-execution-mode.md)).

## Benchmarking

- **[`flowbench`](../../benchmarks/flowbench/)** (free) — Layer 2: every flow routes to the right
  tier, runs the canonical states in order, grounds when it should, and executes (probed). Coverage +
  tier-spectrum gates ⇒ no flow untested.
- **[`corgictionbech`](../../benchmarks/corgictionbech/)** (free floor + live) — Layer 1: the
  monitor→regulate table, the apply/observe channel, the pinned guard, and a live stress test on hard
  complex problems (`solve_rate` + `meta_engagement`). It is what reveals whether metacognition
  actually engages on complex flows.

## Related

- [Architecture](./01-architecture.md) — the OX/OY plane the states + tiers live on.
- [Intent & Paths](./02-intent-and-paths.md) — how a path recognizes intent and selects the tier.
- [Reply Flow](./03-reply-flow.md) — collectors → the terminal `respond` state.
- [Reasoning as a Tool](./08-reasoning-as-a-tool.md) — the reason→write→enact seam metacognition uses.
- [Metacognition](./11-metacognition.md) — the Layer-1 supervisor that shapes the rail.
- [Subagent Fan-out](./12-subagent-fanout.md) — `act` over subjects as parallel sub-thinkers.
- [Task Execution Mode](./13-task-execution-mode.md) — the long-rail generalization.

## Design principle

One flow, graded by complexity, built from a small vocabulary of reusable states — and a
metacognition layer that grows the rail to fit the problem. Simple stays cheap; complex expands the
same states. That is what fits almost any agent, from a one-line answer to a multi-step solve.
