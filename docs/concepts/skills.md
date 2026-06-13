# Skills — Standard Operating Procedures the agent can run

> **What a skill is.** A skill is a **Standard Operating Procedure (SOP)**:
> procedural knowledge, *progressively disclosed*, loaded as a **folder whose index is
> `SKILL.md`** (with sibling reference files). Real SOPs are written as prose for humans —
> mostly unstructured. The SDK's job is to **standardize and parse that prose into a
> structure that drives the reasoning process**: phases, ordered steps, the instruction
> for each step, decision points, required tools. The agent then *runs* the procedure
> instead of re-reading free text every turn.
>
> *Messy SOP in → reasoning-driving structure out.*

This doc is the canonical reference for skills. It covers what exists today and, in the
"implementation status" style of [`react-context-management.md`](./react-context-management.md),
the forward-looking design. Sections scattered across [`api.md`](../api.md) (§ Skills),
[`architecture.md`](./architecture.md), and [`agent-core-overview.md`](./agent-core-overview.md)
should funnel here.

---

## 1. Mental model

A skill is `Activable` — it shares the uniform surface with lobes, stages, and flows:
an `id`, a `use_when` (what it's for), and a `signal` (whether to surface it this turn).
See [`api.md` § Skills](../api.md).

The agent reads an SOP the way a person reads a runbook:

```
see the title in an index  →  open it  →  skim the table of contents  →  read only the step you need  →  act
```

It does **not** dump the whole runbook into the prompt. Disclosure is progressive
(RFC 0013): a skill announces itself with one line; its body and reference files are
pulled in only when the agent's reasoning commits to using it.

The deeper idea, threaded through the rest of this doc: an SOP is prose, but the engine
wants *structure*. The four design threads are all in service of that —

1. **Smart skill-result injection** (§8) — skill reads flow through the same funnel/CDS
   tiering as any tool result, so a large runbook never balloons the window.
2. **The SOP compile pipeline** (§4a) — parse the folder once into a richer structure
   (section tree + procedure graph + search index) loaded into the registry.
3. **Skills as long-running multi-stage procedures** (§4b) — a skill carries progressive
   structure *and* the instruction for each step, and can run across many fires on the
   task rail.
4. **Rich indexing** (§5) — the agent searches the index for the one step it needs and
   injects just that into its reasoning stream.

---

## 2. Core or plugin?

Both — but cleanly split.

| Layer | What | Where | Core or plugin |
|---|---|---|---|
| **Skill machinery** | registry, the `skill_select` / `skill_active` lobes, the `ActivateSkill` activation tool, layered reading (`split_sections`/`file_toc`/`search_bundle`), prompt building | `agent_sdk/skills.py`, `agent_sdk/lobes/skill/` | **Core** — not a plugin |
| **Skill content (the SOPs themselves)** | individual `SkillPack`s / `SKILL.md` folders | plugins via `setup.add_skill(...)`; or DB rows via `SkillRegistry.from_rows` | **Plugin / DB** |

The **subsystem is core**: the lobes, tools, and registry live in `agent_sdk/` and are
always present (see [`api.md`](../api.md): "The **core** network … lives in
`agent_sdk/lobes/` and is not a plugin"). **Individual skills are content** a plugin
contributes (`AgentSetup.add_skill`, see [`plugins.md`](./plugins.md)) or that a
tenant/bot overlays from the DB.

Layering rule — **later source wins by slug** (`SkillRegistry.from_rows`,
`agent_sdk/skills.py:147-208`): DB rows override the plugin/builtin pack of the same
slug, so a bot can specialize a system SOP without forking code.

---

## 2a. Skills is a core module — encapsulated, testable, benchmarkable

Skills is **core, not a plugin.** The whole point of keeping it core is that it can be
*encapsulated behind a narrow contract* and therefore **tested, benchmarked, and improved
in isolation** — the same way `react/funnel.py` or the context builder are improved. The
customization story (skill v2, DB-loaded skills) is achieved by overlaying *content* on
this core module, **not** by turning the subsystem into a plugin.

### The module boundary

The rest of the engine talks to the skill module through a small, stable surface — it does
not reach into its internals:

| Concern | Public surface | Lives in |
|---|---|---|
| What skills a bot has | `SkillRegistry` (`active_for_policy` / `active_for_stage` / `get`) | `agent_sdk/skills.py` |
| Render the per-stage skill prompt | `build_skill_prompt_block(...)` | `agent_sdk/skills.py` |
| Navigate a bundle | `split_sections` / `file_toc` / `file_purpose` / `search_bundle` | `agent_sdk/skills.py` |
| Surface + drive skills in a turn | the `skill_select` / `skill_active` lobes | `agent_sdk/lobes/skill/` |

Everything behind that surface — ranking, the on-demand directive, section slugging, the
state machine — is an implementation detail free to change as long as the contract holds.

### Why this is testable

The core of the module is **deterministic and pure** — no live LLM needed:

- `split_sections`, `file_toc`, `file_purpose`, `search_bundle` are pure functions of their
  input markdown → exact unit assertions.
- `build_skill_prompt_block` / `_rank_on_demand_skills` are deterministic given a query +
  weights (the `ranking_out` hook even exposes per-skill `{l1, l2, activation, kept}` for
  assertions).
- The lobe `state(...)` / `activation(...)` / `state_machine()` resolve from explicit
  flags, so each state transition is unit-checkable without a model.

This is exactly what keeps the module improvable: change the ranking, re-run the unit
suite, and nothing else in the engine has to move.

### Why this is benchmarkable

Behavior that *does* need a model is gated by dedicated benchmarks, so improvements are
measured, not asserted by hand:

- [`skillbench`](../../../../benchmarks/skillbench/) — the skill-readiness gate: activation
  recall (does the model activate the right SOP?) and uplift, label-scoped
  `READY` / `NOT_READY` / `UNMEASURED`.
- [`agentbench`](../../benchmarks/agentbench/) — the live integrated bench; `hard.bounded_context`
  guards that skill reads stay inside the funnel's budget.

The improve loop is: *change the module → unit suite stays green → re-run skillbench /
agentbench → keep the change only if the score holds or rises.*

### Customization without un-coring the module

A user can still version or replace a skill — that's a **content overlay**, orthogonal to
the module being core:

- **Skill v2 / DB skills** — DB rows override builtin/contributed packs **by slug**
  (`SkillRegistry.from_rows`, later-wins-by-slug); a host passes DB-loaded skills in and they
  win over defaults of the same slug. The engine is untouched.
- **Add a domain SOP** — a plugin may still *contribute content* via `setup.add_skill(...)`
  ([`plugins.md`](./plugins.md)). It feeds the core registry; it does not replace it.

So: **machinery is a core, encapsulated, benchmark-gated module; the SOPs themselves are
swappable content.** That split is what lets us test and improve the engine while bots
freely customize their skills. ("Rows, not branches" — see [`architecture.md`](./architecture.md).)

---

## 3. The `SKILL.md` standard

A skill is authored as a YAML-frontmatter + markdown-body document. It has two
interchangeable homes:

- **In code** — a `Skill(...)` literal (the SDK façade, `agent_sdk/skill_def.py`).
- **On disk** — a `SKILL.md` file in a skill folder, parsed by the agent-core loader
  (`packages/agent-core/agent_core/skills/loader.py`).

Both compile to the same runtime `SkillPack` (`agent_sdk/skills.py:23-62`).

### Frontmatter schema

```yaml
---
name: Code review                 # display name (required)
slug: code_review                 # stable id; defaults to the folder name
description: Review a pull request for logic, tests, security.   # one-liner for the index (required)
stages: [simple_answer, synthesize]   # which engine stages the skill is live in
required_tools: [search, kb.read_chunk]  # tools the SOP needs
injection: on_demand              # "eager" (inline the body) | "on_demand" (model calls ActivateSkill)
checklist:                        # optional: ordered procedure steps (→ §4b)
  - key: scope
    title: "Establish scope"
    ask: "Which files changed?"
    input: files
    required: true
    terminal: false
context_vars:                     # optional: per-skill durable workspace (→ §4b)
  - key: findings
    type: notes
    title: "Findings"
---
SKILL: Code review

Markdown body — the SOP itself. Structure it with #/##/### headings (→ §4).
```

| Field | Type | Meaning |
|---|---|---|
| `name` | str (required) | Display name. |
| `slug` | str | Stable id (referenced from `BotPolicy.capabilities.skills`); defaults to the folder name. |
| `description` | str (required) | One-liner shown in the skill index. |
| `stages` | list[str] | Engine stages where the skill is available (default `["simple_answer"]`). |
| `required_tools` | list[str] | Tools the SOP relies on. |
| `injection` | `eager` \| `on_demand` | `eager` inlines the full body; `on_demand` shows only the index entry + a directive to call `ActivateSkill`. |
| `checklist` | list[dict] | Ordered procedure steps (→ §4b). Each: `key/title/ask/input/required/secret/terminal`. |
| `context_vars` | list[dict] | Per-skill workspace state (`checklist`/`todos`/`notes`/`var`) re-pinned each turn while the skill is active. |

The on-disk loader (`load_skill_pack`) reads `SKILL.md` (frontmatter via `yaml.safe_load`
+ body) and slurps sibling `*.md`/`*.txt` files into the pack's `files` dict; binary/
unknown entries are skipped. The pack `id` is the frontmatter `slug` (else the directory
name).

---

## 4. Chunking: sections, ToC, multi-file bundles

The authoring discipline that makes an SOP navigable instead of a wall of text:

- **`SKILL.md` is the *map*.** Keep it short — the procedure outline and the pointers.
- **Depth goes in reference files** (`files={...}`): `GUIDE.md`, `CHECKLIST.md`, etc.
  Loaded automatically from sibling files in the folder.
- **Structure with `#`/`##`/`###` headings** so the runtime can address sub-parts.

The deterministic layered-reading primitives (`agent_sdk/skills.py`):

| Primitive | What it does |
|---|---|
| `split_sections(markdown)` (`:393`) | Splits a file by `#`/`##`/`###` into `Section(id, heading, content, line_count)`; content before the first heading becomes a synthetic `intro`. Section ids are slugified headings, deduped with `-2`, `-3` … |
| `file_toc(content)` (`:428`) | A table of contents — one line per section with its id, heading, and `~tokens`. |
| `file_purpose(content)` (`:436`) | A one-line purpose (frontmatter `description`, else first heading) for the file index. |
| `FULL_FILE_TOKENS = 1500` (`:368`) | The size gate: a file at or below this is returned whole; above it, a bare read returns the `file_toc` and the model asks for one section. |

```
skill index  →  pick a file  →  file > 1500 tok? → ToC  →  request one section  →  read it
                              → file ≤ 1500 tok? → whole file
```

This is what keeps a large multi-file SOP from flooding the prompt: the model navigates
hierarchically, paying tokens only for the section it actually needs.

---

## 4a. The SOP preload / compile pipeline — prose → reasoning structure

> **Status: today is parse-at-load (shallow). The procedure graph + cached index are
> Design.** This section is the heart of the skills design.

A skill folder is an SOP bundle. Before any turn runs, it goes through a load step. The
question is *how much structure we recover, and when*.

### The folder shape (today)

```
skills/code_review/
  SKILL.md          ← the index / map  (frontmatter + body)
  GUIDE.md          ← reference depth
  CHECKLIST.md      ← reference depth
```

`load_skill_pack` already loads exactly this: frontmatter + body + an `rglob` of sibling
text files into `files`.

### Current: parse-at-load, shallow

The loader yields a **flat `SkillPack`** — frontmatter fields, the raw markdown body, and
a `files` dict of raw strings. Structure is recovered only **lazily and per query**:
`split_sections` re-parses the markdown every time `search_bundle` or a section read runs.
Crucially, **nothing extracts the *procedure*** — the steps and their order live as prose
in the body.

### Proposed: SOP compile (Design)

A one-time **compile step** standardizes and parses the (mostly unstructured) SOP into a
richer `CompiledSkill` whose structure *drives reasoning*, not just lookup. Three layers:

1. **Section tree** *(deterministic; exists today as the lazy version)* — `split_sections`
   across `SKILL.md` + every file, precomputed once, with the headings path and per-section
   token counts. The navigable map.

2. **Procedure graph** *(the standardization step; Design)* — normalize prose into ordered
   **phases / steps**, each carrying:
   - its **instruction** (what to do at this step),
   - an optional **decision / branch** ("if X → step k"),
   - **required tools**, **inputs / outputs**, and a **checkpoint**.

   This maps a free-form runbook onto the `checklist` / `context_vars` schema (and the
   proposed per-step `instructions`, §4b) so the engine can drive it step-by-step.
   Best-effort + author-assisted: an explicit frontmatter `checklist` is **authoritative**;
   for prose-only SOPs an extractor (LLM or deterministic) *proposes* a structure the author
   confirms — cache-keyed, the same pattern the codebase already uses for the xlsx→md digest.

3. **Search index** *(Design beyond the lexical fast path)* — lexical postings now
   (`search_bundle`), optional cached per-section embeddings later (reusing the bge-small
   `embed_one` seam), so per-turn search/read hit the index, not the parser.

```
folder (SKILL.md + files)
   → parse           (frontmatter + body + files)            [today]
   → standardize     (prose → procedure graph)               [Design]
   → structure       (section tree + per-section tokens)     [today, lazily]
   → index           (lexical now · cached embeddings later) [partly today]
   → load into registry (CompiledSkill)                      [Design]
   → drive reasoning (search / read / advance)
```

**Why compile.** It preserves the compression invariant (only sections/memos cross stage
boundaries) and cache stability (the compiled prefix is byte-stable), and it turns
reasoning over an SOP from *"re-read the prose each turn"* into *"advance the parsed
procedure."*

---

## 4b. Skills as long-running, multi-stage procedures

A skill is not one flat block. It carries progressive structure *and* the instruction for
each step, in two shapes:

### Stage-scoped instruction (today)

`stages=(...)` controls **which engine stages** a skill's body is injected into. One skill
can therefore contribute different guidance as the turn moves `recall → synthesize` — i.e.
instruction-per-stage. `SkillRegistry.active_for_stage(policy, stage_id)` resolves it.

### Multi-step procedure on the task rail (today via `checklist`, generalizing — Design)

The `checklist` materializes into `bot_task_todos` and is driven across **many continuation
fires** by the `task_execute` flow + `task_execution` lobe — see
[`task-execution-mode.md`](./task-execution-mode.md). Each step has its own
`ask`/`title`/instruction. The `context_vars` (checklist / todos / notes) are the
**per-skill durable workspace**, re-pinned every turn by the `skill_active` lobe and
persisted under `skill:<id>:<key>` (`render_context_var`, `agent_sdk/skills.py:65`).

```
step₁ (instruction) ─advance→ step₂ (instruction) ─advance→ … ─→ done
        │                          │
   context_vars  ← carried state → context_vars      ## Memory index = cross-fire read-back
```

**Proposed (Design):** a first-class per-step `instructions` on checklist entries, so a
phase carries *both* its structure node *and* the reasoning instruction for that phase.
The skill then reads as a procedure: *step → its instruction → advance → next step*,
surviving across fires via the conversation-scoped scratchpad. This is the skill ↔
task-execution bridge — **not a second runtime**; it's the existing `task_execute` flow,
fed structure.

---

## 5. Smart activation & the skill tools

### Activation strategies (`policy.skill_strategy`)

`build_skill_prompt_block` (`agent_sdk/skills.py:268`) renders the per-stage skill section.
Eager skills inline their full body; on-demand skills contribute a one-line index entry
plus the directive. How the on-demand list is chosen:

| Strategy | Behavior | Notes |
|---|---|---|
| `static` *(default)* | The full on-demand index + the pushy `_ON_DEMAND_DIRECTIVE`. | Byte-identical legacy behavior. |
| `adaptive` | Rank on-demand skills by `score_relevance` to the turn query; trim below `skill_min_activation` but always keep the top `_SKILL_MIN_KEEP = 3` (high-recall floor). | `_rank_on_demand_skills` (`:326`). Eager skills are never trimmed; a trimmed skill stays registered and activatable by slug. |
| `reason` | An LLM step writes the chosen slugs → passed as `active_slugs`, narrowing the list to those skills. | Narrowing only — never adds an undeclared skill; empty/no-match falls back to all-declared (a flaky reasoner never zeroes the bot). |

The directive is **deliberately pushy** (`_ON_DEMAND_DIRECTIVE`, `:138`): with soft
"before relying on…" phrasing the model routinely skipped activation and free-styled the
task (activation recall measured at 0 on the `code_review` fixture — see
[`skillbench`](../../../../benchmarks/skillbench/)). So it reads: *"you MUST first call
`ActivateSkill` … do not attempt the task from the one-line summary alone."*

### The read/search surface — pull just the needed instruction into reasoning

This is where the agent reads/searches the SOP and injects the result into its reasoning:

- **`search_bundle(query, top_k)`** (`agent_sdk/skills.py:459`) — deterministic
  token-overlap keyword search (NFC-normalized for Vietnamese) across **every section of
  every file** in the active skills. Returns `{skill, file, section, heading, score,
  snippet}`. The fast path through very large bundles; runs over the compiled index (§4a)
  when present.
- **`ActivateSkill`** — the tool the on-demand directive tells the model to call with a
  skill's slug. It loads the skill's full instructions (or a `file_toc` if a file exceeds
  `FULL_FILE_TOKENS`, §4) and flips the skill to **in use** for the turn (the
  `skills_in_use` flag the `skill_active` lobe drives, set at the activation moment). The
  returned text becomes part of the reasoning prompt for that hop.

The loop:

```
search the index  →  read the one relevant section  →  inject into the reasoning stream  →  act
```

How much of that read stays resident in the window is governed by the injection mechanism
in §8.

---

## 6. The lobe state machine

Two coexisting lobes on `LAYER_SKILL`, driven by the skill flags
(`skills_declared / skills_unselected / skills_in_use / has_read_directive`, in
`agent_sdk/lobes/skill/_common.py`). They run side by side: one drives the loaded SOP
while the rest stay listed.

```
skill_select  (NON-SELECTED states)                 skill_active  (SELECTED states)
(none) ─▶ listing ─▶ selecting ─▶ activating   ─▶   activated ─▶ driving
          skill:list  select:cue   read:hint         in_use      guide + context_vars
```

| Lobe | State node | Emits |
|---|---|---|
| `skill_select` | `skill:list` | The visible skill index (one node per active skill). |
| (`:75`) | `skill:select:cue` | "No skill selected yet — pick one and activate it before answering." |
| | `skill.read:hint` | "Skill X is a strong candidate — activate it with `ActivateSkill`." (when a skill declares a read directive) |
| `skill_active` | `skill:in_use` | The "N skills in use" marker. |
| (`:54`) | `skill:guide` | The drive instruction: execute the SOP's remaining steps in order, reading referenced files/sections only as each step needs them, until done. |
| | `skill:context_vars` | The active skill's **pinned** workspace block (checklist/todos/notes) — its live metadata, recomputed every turn. |

---

## 7. Worked simulation — a large multi-file SOP

An agent with a `code_review` skill (`SKILL.md` ~2,200 tokens + `GUIDE.md` ~3,000 tokens),
`injection: on_demand`. The user says *"review PR #412."*

```
Turn opens
  skill_select → injects ONE line:  "- Code review: Review a PR for logic, tests, security."
                 + the ActivateSkill directive.            ← bodies NOT loaded; ~30 tokens
  (optional) model calls search_bundle("security checklist")
                 → {file: GUIDE.md, section: security, snippet: …}   ← finds where to look

Model commits
  calls ActivateSkill("code_review")
  SKILL.md is 2,200 tok (> 1,500) → returns its file_toc, not the whole body:
     "- [scope] Establish scope (~180 tok)
      - [logic] Logic review (~620 tok)
      - [security] Security review (~410 tok) …"
  skills_in_use flips to ["code_review"].

Next hop
  skill_active → injects the in-use marker + drive guide
              + the PINNED context_vars (the checklist: [todo] scope, [todo] logic, …)
                                                          ← the SOP's metadata, in context

Driving the procedure
  model reads sections on demand:
     ActivateSkill/read("code_review", "GUIDE.md", "security")  → that section's text
  each read is a tool_result → flows through the funnel (§8):
     newest section stays FULL; the previous one demotes to a one-line hint.
  checklist advances [done] scope → [doing] logic → …  (persisted under skill:code_review:checklist)
```

The prompt grows when a section is read, then **funnels** as the model moves on — it never
balloons to "the whole runbook resident at once."

---

## 8. Smart injection of the skill-result back into the prompt

> **Current: skill reads funnel like any tool result (Done). Skill-section value-pinning is
> Design.**

An activated skill's content arrives as a **tool result** (the `ActivateSkill`/section
read), so it rides the same context-efficiency machinery as everything else — the funnel
(`agent_sdk/react/funnel.py`), wired in the engine's per-hop injection decision
(`agent_sdk/engine.py:954-1002`).

**Today:**

- `tier_observations` keeps the newest observation(s) **full** (the model is acting on
  them) and demotes older, spent ones to a one-line hint (`SPENT_MARKER` — "what was
  called → gist; re-read to expand"). The `tool_use ⇄ tool_result` pairing is preserved
  and the stable prefix is untouched, so the **prompt cache survives**.
- `compact_observations` bounds the tail: it collapses spent-hint pairs older than
  `max_spent` into one rolling summary, and (when memory is on) the
  `compaction_summarizer` offloads the spent body to flash memory and returns a digest that
  **names a re-fetchable handle** — so a compacted section is recoverable via `recall`
  without keeping it resident.
- `score_observations` (value-aware, opt-in via `working_set_budget`) pins the highest-CDS
  observations by the current stage goal — **CDS = relevance / cost** — so a goal-critical
  older section survives a newer-but-off-goal one. Value beats recency.

This maps onto the 3-tier exposure router from
[`react-context-management.md`](./react-context-management.md) and
[`tool-use-at-scale.md`](./tool-use-at-scale.md):

```
inject-full   high CDS, fits budget        — the live SOP step the model is executing
hint + tool   medium / too-large           — a one-line hint + re-read affordance (discoverable, never dropped)
offload        low / speculative           — body in memory, named by a handle
```

**Proposed (Design):** treat the **active SOP as a first-class value-pinned source** — its
in-use sections get a utility prior so the funnel protects the live runbook over incidental
tool chatter; `context_vars` stay pinned (already true); and section re-reads dedupe against
the memory handle instead of re-injecting the raw body. The gate this must not regress is
agentbench's `hard.bounded_context` (an 18-call tool loop whose `funnel_obs_chars` peak must
stay bounded — see [`agentbench`](../../benchmarks/agentbench/)).

---

## 9. Implementation status

| Feature | Status | Where |
|---|---|---|
| Layered reading — `split_sections` / `file_toc` / `search_bundle` | **Done** | `agent_sdk/skills.py` |
| On-disk loader — `SKILL.md` folder → `SkillPack` | **Done** | `agent_core/skills/loader.py` |
| Three activation strategies — `static` / `adaptive` / `reason` | **Done** | `agent_sdk/skills.py` |
| Skill lobe state machines — `skill_select` / `skill_active` | **Done** | `agent_sdk/lobes/skill/` |
| Skills as an encapsulated core module (narrow contract; deterministic core is unit-testable) | **Done** | `agent_sdk/skills.py`, `agent_sdk/lobes/skill/` |
| Skill content overlay — v2 / DB skills override by slug (no engine change) | **Done** | `SkillRegistry.from_rows`, `agent.py:202-213` |
| Benchmark-gated improve loop (skillbench activation/uplift; agentbench bounded-context) | **Done** | `benchmarks/skillbench`, `benchmarks/agentbench` |
| Funnel tiering / compaction / CDS pinning of tool results | **Done** | `agent_sdk/react/funnel.py`, `engine.py` |
| `checklist` → task rail across fires | **Partial** | `checklist` + `task_execute` exist; scratchpad landed Phase 1 ([`task-execution-mode.md`](./task-execution-mode.md)) |
| SOP compile — `CompiledSkill` (section tree + cached index/embeddings) | **Design** | §4a |
| SOP standardization — prose → procedure graph (phases/steps/branches/checkpoints) | **Design** | §4a |
| Per-step `instructions` on checklist entries (procedure phases) | **Design** | §4b |
| Skill-section value-pinning in the funnel (SOP as first-class pinned source) | **Design** | §8 |

---

## 10. See also

- [`api.md`](../api.md) — § Skills, § Tools, § Plugins (the public surface).
- [`plugins.md`](./plugins.md) — how a plugin contributes skills (`add_skill`).
- [`react-context-management.md`](./react-context-management.md) — the 3-tier exposure
  router and CDS the injection mechanism (§8) builds on.
- [`tool-use-at-scale.md`](./tool-use-at-scale.md) — tool-result lifecycle, read-back.
- [`universal-memory.md`](./universal-memory.md) — the store skill bodies offload to.
- [`task-execution-mode.md`](./task-execution-mode.md) — the rail that runs multi-stage SOPs.
- [`intent-and-paths.md`](./intent-and-paths.md) — how a turn's intent biases which skills surface.
- [`agentbench`](../../benchmarks/agentbench/) — the live bench gating bounded context.
- [`skillbench`](../../../../benchmarks/skillbench/) — skill activation-recall gate.
