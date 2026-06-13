# Intent = Path: how recognition, flow, and lobes relate

> Companion to `architecture.md`. That doc describes the three optimization
> axes (OY lobes, OX flows, metacognition). This doc answers a narrower,
> recurring question: **what is a "path", how does it relate to a "flow", and
> why does one user intent feel like it is defined in two places at once?**

## TL;DR

- **The path *is* the intent.** A path module (`lobes/paths/<x>.py`) recognizes
  "what kind of turn is this?" — dynamically, every turn, from free signals.
- **A recognized path applies to BOTH axes:**
  1. it **biases lobes** (OY) — nudging *what context* is gathered;
  2. its **name selects the flow** (OX) — choosing *which pipeline* executes.
  Metacognition sits above and may regulate the next step.
- There is **no axis conflict**. The friction people feel is that **one
  intent's definition is physically split across ~5 files**, coupled only by a
  shared string name, with nothing keeping the halves consistent.

## The mental model (precise)

```
            ┌─────────────── a turn ───────────────┐
 query ──▶  recognize paths  ──▶  resolve path  ──▶  the intent (e.g. "research")
            (free B1 signals)      (highest score        │
                                    over threshold)       ├─▶ biases lobes  (OY)  → what context
                                                          └─▶ selects flow  (OX)  → which pipeline
                                                                                      │
                                                                  metacognition watches & regulates
```

Concretely, in code:

- **Recognition** — `recognize_paths(ctx, paths)` scores every `PathSpec`,
  `resolve_path(...)` picks the winner (or `"emergent"`). Pure, deterministic,
  no LLM. (`agent_core/lobe_network.py`.)
- **Apply to lobes (OY)** — a recognized path adds `path_<name>__<lobe>` bias
  to its `members` inside the activation formula
  (`propagate`, `lobe_network.py` ~L696-713). Biasing, never gating — a wrongly
  biased lobe still needs its own signals to fire.
- **Apply to flow (OX)** — the resolved path *name* selects the pipeline via
  `FlowRegistry.steps_for_path(name)` (`flows/registry.py`). Same string,
  different tree.

**Dynamic, not a static lane.** Recognition runs every turn on live signals. A
recognizer can be *lexical* (e.g. `qna` reads interrogative cues) or
*structural* (e.g. `onboarding.recognize` returns `1.0` only when the
`config_mode` flag is set by the worker). The structural case is still
dynamic — it reads a per-turn flag — it just isn't competing on word cues. This
is why "admin is never confused with another task": its recognizer dominates on
a flag, not on fuzzy lexical scoring.

## The real problem: split definition (not axis overlap)

One intent's knowledge is scattered, glued only by a shared name:

| # | Where | What it holds for the intent |
|---|---|---|
| 1 | `lobes/patterns.py` | the regex cues the recognizer reads |
| 2 | `lobes/paths/<x>.py` | the recognizer + lobe bias (`members`, `bias`) |
| 3 | `flows/defaults.py` + `flows/stages/*.py` | the pipeline + per-step `tools` allowlist (same name `<x>`) |
| 4 | a SKILL `required_tools` + `cli.py` mode-union | the intent's skills/tools |
| 5 | task templates | discovery not scoped to the intent |

Nothing structurally ties these together or checks them. Two concrete symptoms
found in the live tree:

### Symptom A — the path↔flow relationship is implicit (research / `classify`)

`lobes/paths/research.py` declares `members=("classify", "plan", "research",
"synthesize", "cite", "filter")` and biases `classify`. But the `research`
**flow** (`flows/`) has steps `plan → research → synthesize → cite → filter` —
**no step consults `classify`**. This is actually *correct*: `classify` is a
layer-4 lobe that fires in the **pre-flow classification stage**, so the path is
applying a legitimate *cross-stage* bias (research-shaped queries should nudge
the classifier toward "complex"). The problem is that **nothing in the code says
so.** A path's `members` legitimately span pre-flow lobes (`classify`,
`condense`) *and* flow-step lobes — but the two lists live in two independently
edited files with no documented link and no test, so a real typo or a stale
member is indistinguishable from an intentional cross-stage bias. The contract
is implicit; you have to reverse-engineer it.

### Symptom B — the same list written three times (admin tools)

The six `admin.*` tool names are the contract in
`arag_core/mcp/admin_tools.py` (`list_tool_specs()`), and are then **hand-copied**
into two more places that must be kept in sync by hand:

1. `apps/backend/app/seed/skills/admin_management/SKILL.md` → `required_tools: [admin.overview, … , admin.update_persona]`
2. `packages/agent-core/agent_core/flows/stages/*.py` → the onboarding step's `tools=( "admin.overview", … )`

(plus `cli.py` `_CONFIG_MODE_SKILLS` hard-codes the skill *slug*). Add a 7th
admin tool and you must remember three edits in three packages; miss one and the
tool silently never reaches the model (the flow-step `tools` allowlist is a hard
filter). There is **no single source of truth for "the admin intent."**

That scatter is the "mixed concern": recognition (path) and execution (flow)
for one intent look like the same thing cut in half and filed in two trees.

## The design: path stays in `lobes/`, writes intent to context, flow reads it

Intent is a **dynamic, soft, per-turn guess from content** — possibly *several*
intents in one conversation — whose only job is to **adapt** the flows and the
lobes to the user's need. It is not a router, not a static lane, not a hard
switch. Because the guess adapts **both** axes, recognition is a signal that
sits *above* both — so it must not be owned by either.

**Do not relocate paths into `flows/`.** That was considered and rejected: a path
is part of the lobe-activation pass (paths live in `LobeRegistry`; `propagate`
recognizes intent and biases lobes in one computation), and recognition draws on
a substrate (`patterns`) that a lobe (`classify`) also uses — so folding paths
into flows creates an import cycle and splits recognition from the pass it
belongs to. Likewise, do **not** add an `IntentSpec`/`Lane` concept.

**The clean seam is the existing Blackboard (context bus):**

```
  lobe pass (recognition lives here)                 flow pipeline
  ┌───────────────────────────────┐                 ┌──────────────────────┐
  │ path recognizers score intent │── writes ──▶ [ intent context node ] ──│ reads ▶ adapt flow
  │ → bias lobes (OY)             │   (Blackboard:   {name, scores{...}})   │  steps to the guess
  └───────────────────────────────┘   the neutral bus)                     └──────────────────────┘
```

- **Path stays in `lobes/`** — it runs in the activation pass and biases lobes,
  which is its OY job.
- **Path writes the intent to context** — the recognition result (the winner
  *and* the full score vector, so multiple intents are carried) is written as a
  context node on the Blackboard (`lobe_network.Blackboard`).
- **Flow reads the intent from context** — the pipeline selects/adapts its steps
  by *reading* the intent node, instead of receiving the path name out-of-band.

This makes path→flow a pure **data** dependency through the Blackboard: `flows/`
never imports paths, paths never import flows, and recognition is produced once
and consumed by both axes. Engineering-wise the engine is already ~90% here —
the Blackboard exists and is the cross-step bus; today only the resolved path
*name* is passed as an interpreter local (`interpreter.py` `pipeline_route`)
rather than read from context. Routing it through the intent context node closes
the gap and, by carrying the score vector, opens the door to **multi-intent
flow adaptation** (today lobes already blend every recognized path's bias; the
flow still picks a single winner).

Remaining single-source cleanups (independent of the above, no relocation):
- **Per-intent skills/tools declared once** — derive the flow-step `tools`
  allowlist + SKILL `required_tools` from the one tool contract (kills the admin
  triplication).
- **Drift-proof tests** (already added, `tests/test_intent_consistency.py`):
  path↔flow name bijection, members reference real lobes, structural recognizers
  are flag-gated.
- **Templates stay dynamic via the existing `skill_slugs`** — a template surfaces
  when its declared skills are active this turn; admin templates declare
  `skill_slugs:["admin_management"]`. No new "lane" field.

The three axes and their separate `path_*` / `flow_*` / lobe weight namespaces
are unchanged — you still tune one axis without touching the others. What changes
is that the intent guess becomes an explicit piece of **context** that both axes
read, so adding/adjusting an intent is a recognizer + a context contract, never a
cross-tree edit.
