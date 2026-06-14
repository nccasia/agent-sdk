# Prompt Engineering

> The kernel already wraps every context section in an XML tag for free — `<instructions>`,
> `<stage_prompt>`, `<synthesize>`, `<env>`. This doc is about the **words inside the tags**. Good
> prompt craft here is not authoring one monolith; it's writing each lobe / stage / instruction
> *contribution* well, and letting the engine compose, order, and tag them deterministically.

## The idea

A PreAct system prompt is **assembled, not authored**. Each turn, `_compose_system_segmented`
(`agent_sdk/engine.py`) collects a list of `(source, text, stability)` segments and renders each one
into an XML tag:

- `instructions` — the agent persona, from `PreactAgent(instructions=…)` → `Engine.instructions`.
- `memory_directive` — the `MEMORY_DIRECTIVE` addendum (`agent_sdk/agent.py`) → `system_addendum`.
- `stage_prompt` — the active stage's `system_prompt` (e.g. `_PLAN_PROMPT`, `plugins/planning/stages.py`).
- per-lobe contributions — whatever each active lobe returns from `prompt()` (e.g.
  `SynthesizeLobe.SYSTEM_PROMPT`, `cognition/lobes/synthesize.py`).
- `env` — the date/time line from `datetime_block` (`agent_sdk/lobes/runtime.py`), tagged `<env>` via
  `_XML_TAG_MAP`.

So "prompt engineering" in this SDK means: **write each contribution to be clear, direct,
exemplified, and contradiction-free**, knowing the kernel owns structure, ordering, tagging, and
provenance. You never hand-roll the `<tag>` wrappers; you never concatenate sections yourself. You
own one string at a time, scoped to one lobe or stage.

This is the OY axis ([architecture](./01-architecture.md)) seen from the text side: a lobe fires the
right *context* **and** the right *local prompt behaviour* for one slice of the turn.

## The techniques

Each row is a standard prompt-engineering technique, the SDK seam that applies it, and how well the
shipped prompts already use it.

| Technique | What it means | SDK seam | Status |
|---|---|---|---|
| Structure with XML tags | Delimit each section so the model parses it reliably | `prompt_format="xml"` (default) / `_xml_tag` (`engine.py`) | **live — automatic** |
| Role / persona | State who the agent is and its standing rules | `PreactAgent(instructions=…)` (a `stable` contribution) | live |
| Be direct & specific | Say exactly what to do; prefer positive framing ("do X") over negative ("don't do Y") | any contribution string | **partial** — `_PLAN_PROMPT` leans on "Do NOT do the work" |
| One example of the target shape | Show a well-formed instance (few-shot) of the output you want | the contribution string | **gap** — no shipped prompt shows a model TodoWrite / memo |
| Explicit output contract + refusal floor | Define the output format and when to refuse instead of guessing | `SynthesizeLobe.SYSTEM_PROMPT` is the exemplar | live — good |
| Room to reason, then pin the finish | Let collector stages think; keep terminal `cite`/`filter` at `temperature 0` | stage loop mode + `PINNED_LOBES` | live |
| No internal contradiction | One contribution must not tell the model two opposite things | author discipline | **gap** — `_PLAN_PROMPT` says "PLAN ONLY" then "skip the plan and just answer" |
| Token economy / density | Keep the gist in the prompt, offload the detail | the funnel ([04](./04-react-context-management.md), [05](./05-tool-use-at-scale.md), [06](./06-universal-memory.md)) | live |
| Progressive disclosure | Pull procedure text in only when the agent commits to it | skills ([09](./09-skills.md)) | live |
| Stability tagging | Mark a block `core` (don't touch) vs `tunable` (safe to tune) | `PromptContribution.stability` ([contracts](../contracts.md)) | live |

The takeaway: the SDK already gives you structure, density, disclosure, and stability for free. The
remaining wins are in the *wording* of individual contributions — directness, examples, and internal
consistency.

## The mechanism: contribute → compose → tag

A contribution is never wrapped or ordered by its author. It is handed up as `(source, text,
stability)` and the kernel does the rest — mirroring the `reason → write → enact` split of
[reasoning-as-a-tool](./08-reasoning-as-a-tool.md):

```txt
   lobe.prompt() / stage.system_prompt / instructions
        │  returns (source, text, stability)              (contribute — you own the words)
        ▼
   _compose_system_segmented (engine.py)
        │  orders segments, tracks provenance              (compose — the kernel owns the shape)
        ▼
   _xml_tag(source) ──▶ "<stage_prompt>\n…\n</stage_prompt>"   (tag — _XML_TAG_MAP / default name)
        │
        ▼
   one system prompt  +  per-segment provenance (the viewer colours by source)
```

Because every segment keeps its `source`, the inspector can colour the composed prompt by which lobe
contributed each line — so a prompt-craft regression is *attributable* to one contribution, not lost
in a wall of text.

## Worked examples

These use the **real shipped strings** as teaching material — illustrations, not edits.

**`_PLAN_PROMPT` (`plugins/planning/stages.py`) — fix the contradiction, flip to positive, add an
example.** It opens with `PLAN ONLY … Do NOT do the work` but ends with `(For a simple single-step
question, skip the plan and just answer.)` — two opposite instructions in one block. It also relies
on negative framing and shows no example of a well-shaped todo. A cleaner shape:

```txt
Plan this task into a TodoWrite list — one todo per part. For each todo give content (imperative),
status 'pending', activeForm. Design substantial independent steps as their own worker: give a prompt
(how to do it) and the tools it needs. Leave light steps plain (content only). Set deps to the 1-based
indexes a step waits on; omit deps for steps that can run in parallel.
Example: {content: "Benchmark the two parsers", status: "pending", activeForm: "Benchmarking parsers",
          prompt: "Run bench X against parser A and B, report p95", tools: ["Bash"], deps: [1]}
If the task is a single step, answer it directly without a plan.
```

It says what to do (positive), shows the exact todo shape once (few-shot), and resolves the
single-step case as a clause rather than a contradiction.

**`MEMORY_DIRECTIVE` (`agent_sdk/agent.py`) — calm the emphasis, kill the double negative.** It uses
`MEMORIZE` / `RECALL` / `EACH` / `Do not skip` and the double-negative `Never answer that you don't
have something without recalling first`. Emphasis that's everywhere is emphasis nowhere; double
negatives cost the model a parse. Prefer one emphasis per rule and a positive recall instruction:
`Always recall before answering a question about earlier information.`

**`SynthesizeLobe.SYSTEM_PROMPT` (`cognition/lobes/synthesize.py`) — the exemplar.** Copy this
shape. It is a clean **task directive** (no persona — identity comes from `<instructions>`), lists
explicit rules, defines the output contract (clean markdown, no tables), names a refusal floor (`If
all memos have empty claims, refuse to answer rather than guessing`), and handles the false-premise
case. Every contribution should aim for this clarity.

## Layers from Claude Code, mapped to lobes

A mature agent system prompt — Claude Code's is the reference — is not one blob; it's a **stack of
named, single-purpose layers**: identity, safety, tone/style, proactiveness, conventions, task
management, tool-use policy, memory, environment, context management. Each layer is stable, owns one
concern, and is independently tunable.

PreAct's unit for a "layer" is a **lobe contribution** — one source-tagged segment owned by one
lobe ([the idea](#the-idea)). So *supporting a layer* means: a lobe owns it, contributes
well-crafted text, and activates on exactly the turns the layer matters. The table maps each Claude
Code layer to its PreAct owner and the lever to push.

| Layer (Claude Code) | Best practice it uses | PreAct owner (lobe → `<tag>`) | How to maximize |
|---|---|---|---|
| Identity / persona | One terse, stable line: who the agent is + its job | `instructions` → `<instructions>` | One role sentence, marked `core`/stable; never let it drift turn to turn. |
| Safety & scope boundary | Explicit allow/deny with dual-use context; refuse by default | `scope_check` → `<scope_check>` (early gate) + `filter` → `<filter>` (pinned ground-or-refuse) | `filter` is exemplary; **generalize `scope_check`** off its hardcoded FUNiX/Vietnamese text into a templated, per-bot boundary. |
| Tone & style / output contract | Hard limits ("fewer than 4 lines"), no preamble/postamble, GFM, no emoji unless asked; ± examples | `format` → `<format>`, `respond` → `<respond>` | Replace `respond`'s vague "be concrete and direct" with an explicit contract (length, no preamble, register); give `format` channel defaults, not just `{requirements}`. |
| Proactiveness & confirmation | Do the right amount; confirm hard-to-reverse / outward actions; report faithfully | **no owner today** | A real gap — add it as persona `instructions` text or a safety-lobe addendum. |
| Conventions / grounding discipline | Verify before assuming (does the lib exist?); match surroundings; never invent | `research` → `<research>` ("search returns snippets — always `read_chunk` before claims"); `cite` → `<cite>` | `research` already encodes CC's "verify before you assert" — promote that exact wording into every tool-loop lobe. |
| Task management / planning | TodoWrite for multi-step; one item in-progress; plan → implement → verify | `classify` (route) + `plan` → `<plan>` + planning plugin (`todo_list` / `plan_supervise` / `_PLAN_PROMPT`) | Fix `_PLAN_PROMPT` (see [Worked examples](#worked-examples)); `plan` already shows one example aspect — keep that. |
| Tool-use policy | Batch independent calls in parallel; prefer a specialized tool over shell; delegate search to subagents | `tool_select` (adaptive exposure) — but contributes **no prompt** | A gap — `tool_select` is deterministic-only; CC states tool policy *in the prompt*. Add a short tool-use-policy contribution. |
| Memory | One fact per file; an index; record durable facts immediately; recall before answering | `memory_recall` / `session_recall` / `ctxvar_resolve` + `MEMORY_DIRECTIVE` (`<memory>` / `<context>`) | Already the strongest layer; just tighten `MEMORY_DIRECTIVE` wording ([Worked examples](#worked-examples)). |
| Environment / ambient | Inject fresh each turn: cwd, platform, OS, date, model | `datetime_block` → `<env>`; `ctxvar_resolve` → `<context>` | SDK `<env>` is date-only; CC's carries more — widen it with channel / locale / platform when the host has them. |
| Context management | Summarize when long; primacy + recency + rolling summary | `session_recall` → `<session_recall>` + the funnel ([04](./04-react-context-management.md)) | Already covered; keep the summary dense. |

### The shipped canonical order

The composer no longer flat-appends — it sorts every segment into a canonical order
(`_PROMPT_LAYERS` + `_layer_key`, `engine.py`): a **stable instruction prefix** leads
(identity → directives → capabilities → task → output-contract → safety) and the **turn-volatile
tail** trails (context/memory → `<env>`), because the conversation and the user query own recency in
the `messages` array that follows the system prompt. Sorting by stability first makes the volatile
sections a *contiguous suffix* — the cache-prefix boundary docs/04 wants. Two duplications were
removed with it: answer lobes (`synthesize` / `cite` / `filter` / `respond`) no longer re-declare a
persona (identity is the one `<instructions>` layer), and the conversation is no longer re-injected
into the system prompt (it lives once, in `messages`). All of this is gated, no-LLM, by
[`promptbench`](../../benchmarks/promptbench/) (its `structure` tier).

### Two gaps worth a new contribution

The two layers Claude Code states **in the prompt** that PreAct still leaves implicit:

- **Tool-use policy.** `tool_select` shapes *which* tools are exposed but says nothing about *how* to
  use them. CC tells the model to parallelize independent calls, prefer a dedicated tool over shell,
  and read a source before asserting. That belongs as a short `tool_select` (or stage) contribution.
- **Proactiveness & confirmation.** No lobe owns "do the right amount; confirm before a
  hard-to-reverse or outward action; report outcomes faithfully." Today it can only live in the
  `instructions` persona — make it explicit there or give it a small owner.

### Maximizing a lobe's contribution

A contribution only lands when **the lobe activates** *and* **its words are good** — two independent
levers, and a perfect prompt on a lobe that never fires is dead text:

- **Activation (OY tuning).** Set `prior`, `min_activation`, path `bias`, and signal weights
  (`lobes/weights.py`, the path recognizers) so the lobe fires on exactly the turns its layer
  matters. This is the gate before the words are ever seen.
- **Wording (this doc).** A role line + an explicit output contract + one example + only the ambient
  signals the lobe needs, tagged with the right `stability`.
- **One lobe, one layer.** Claude Code's power is that each layer is single-purpose. Mirror it: don't
  let `synthesize` carry tone rules that belong to `format`. One concern → one lobe → one `<source>`
  keeps provenance clean and tuning local.
- **Verify before asserting.** Copy `research`'s "read the full source before forming a claim"
  discipline into any tool-loop lobe — it is the prompt-level form of CC's "don't assume a library
  exists."

## Boundaries

- **Craft never overrides the invariants.** No wording can strip `cite` / `filter`
  (`PINNED_LOBES`); terminal grounding stages stay `temperature 0` regardless of prompt text.
- **Structure belongs to the kernel.** Don't hand-write `<tag>` wrappers, don't concatenate other
  sections, don't reorder — emit one contribution and let `_compose_system_segmented` shape it.
- **One contribution, one source.** A lobe writes only its own segment; it never reaches into
  another lobe's text. Provenance colouring depends on this.
- **Determinism upstream of the words.** Which contributions appear is a pure function of `(spec,
  context)` (intent → lobes → stage); prompt craft tunes the *text* of a contribution, never the
  routing that selects it.

## Implementation status

**Live.** XML composition + provenance (`_compose_system_segmented`, `_xml_tag`, `_XML_TAG_MAP`,
`engine.py`); the persona / addendum / stage / lobe contribution seams; `stability` tagging on
`PromptContribution` (`contracts.md`); density (the funnel) and progressive disclosure (skills) as
token-economy levers. The **canonical layer order** (`_PROMPT_LAYERS` / `_layer_key`: stable prefix →
volatile tail), the **answer-lobe persona de-dup**, and the **conversation de-dup** (transcript in
`messages`, not the system prompt) — all gated by [`promptbench`](../../benchmarks/promptbench/),
which also lints prompt **quality** (rule-based) and scores it with an LLM **judge**.

**To build.** A convention/affordance for attaching one canonical few-shot example to a contribution.
The layer gaps from the Claude Code mapping: a **tool-use-policy** contribution on `tool_select`; a
**proactiveness & confirmation** layer; generalizing `scope_check` off hardcoded tenant text into a
templated boundary; widening `<env>` beyond the date line. (The quality lint that flags double
negatives / ALL-CAPS / multi-role / missing-directive shipped as [`promptbench`](../../benchmarks/promptbench/)'s
`quality` tier.)

**Deferred.** Per-model prompt variants (one contribution, different wording per provider);
auto-compiled contribution surfaces that a tuner can optimize against a bench verdict.

## Benchmarking

[`promptbench`](../../benchmarks/promptbench/) evaluates the prompts on three tiers. **structure**
(free, no LLM) reads `system_segments` and gates the canonical order, identity-once/-first,
`<env>`-last, the volatile tail, and no persona/section/conversation duplication. **quality** (free)
lints each authored prompt constant against this doc's best practices (one role, no double negatives,
an explicit directive, no ALL-CAPS shouting, bounded length) and scores `quality_avg`. **judge**
(live, `--live`) has an LLM rate each prompt on a clarity/specificity/consistency/output-contract
rubric, gating on the aggregate `judge_mean` (it reads fragments out of context, so a single low row
is noise). Behavioural wording is still exercised by the live benches — plan/synthesize in
**delegationbench**, skill prompts in **skillbench**. A prompt rewrite is benchmark-gated: keep
`promptbench` green and re-run the relevant live bench before keeping it.

## Related

- [Architecture](./01-architecture.md) — OY = the lobe axis; a contribution is a lobe's text side.
- [ReAct Context Management](./04-react-context-management.md) — the funnel that makes a prompt earn its tokens.
- [Universal Memory](./06-universal-memory.md) — the gist-in-prompt / detail-offloaded model craft relies on.
- [Reasoning as a Tool](./08-reasoning-as-a-tool.md) — the `contribute → compose → enact` split this mirrors.
- [Skills](./09-skills.md) — progressive disclosure of procedure text into the prompt.
- [`../api.md`](../api.md) — the `prompt_format` surface (XML vs markdown).
- [`../contracts.md`](../contracts.md) — `PromptContribution` and its `stability` flag.

## Design principle

The kernel guarantees the *shape* of the prompt — tagged, ordered, attributed. The engineer owns the
*substance* of each contribution: clear, direct, exemplified once, and free of self-contradiction.
Write one good string at a time; let the engine compose the rest.
