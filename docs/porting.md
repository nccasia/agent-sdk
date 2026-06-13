# Porting PreAct to another language (Rust / Go / JS / …)

> **Status: design / target.** Defines the contracts a non-Python PreAct runtime must honor.
> The reference implementation is the Python `agent_sdk`.

PreAct is portable because it cleaves cleanly into two halves:

1. **A deterministic core** — intent recognition, lobe/stage/flow activation, attention/budget
   selection, and flow resolution. Pure functions of `(spec, context)`: **no I/O, no clock, no
   LLM, no randomness.** Re-implement this from the spec + the algorithm below and you get
   byte-identical routing in any language.
2. **A handful of I/O seams** — the only things that touch the outside world. Implement these
   with your language's native HTTP/DB/queue libraries.

```
         ┌─────────────────────────── your language ───────────────────────────┐
  spec ──▶  DETERMINISTIC CORE (port this exactly)                              │
  (JSON)    recognize → activate lobes → resolve flow → build attention/budget  │
         │        │                                                             │
         │        ▼ drives                                                      │
         │  ENGINE LOOP (port the control flow)                                 │
         │        │ calls ───────────────► I/O SEAMS (implement natively)       │
         │        │   LlmCall · ToolRuntime · Embed · SessionStore ·            │
         │        │   MemoryStore · Workspace · EventSink                       │
         └────────┴─────────────────────────────────────────────────────────────┘
```

If two implementations load the same `preact.spec.json` and feed the same `context`, their
recognition + activation + flow resolution **must** match. That equivalence is the conformance
test (see [Conformance](#conformance)).

---

## 1. The spec (`preact.spec.json`)

The entire agent configuration is data. Every activable (lobe, stage, flow) carries the uniform
`{id, name, description, use_when, signal}` interface; `signal` is serialized as a **declarative
signal expression** (below), not code — so it ports without a host language.

```jsonc
{
  "version": "1",
  "lobes": [
    {
      "id": "classify", "name": "Classify", "description": "route simple/complex",
      "use_when": "every answer-producing turn",
      "layer": 4,                       // 0 instinct · 1 perception · 2 memory · 3 skill · 4 cognition · 5 expression
      "behavior": "select",
      "pinned": false,
      "prior": 0.0,
      "threshold": 0.5,                 // min activation to fire
      "order": 0,
      "writes": ["route"],              // blackboard node kinds it may emit
      "excites": {"plan": 0.3},         // forward edges (must target a strictly later (layer, order))
      "attends": {"kinds": [], "scopes": [], "budget_tokens": 1600, "min_activation": 0.22},
      "signal": { "all": [ {"flag": "is_question"} ] }   // declarative; see §3
    }
    // …
  ],
  "stages": [
    { "id": "research", "use_when": "needs sources", "loop": "agentic",
      "lobes": ["research"], "tools": ["search"], "signal": {"const": 1.0},
      "model": null, "temperature": null, "max_tokens": null, "hops": null }
  ],
  "flows": [
    { "id": "research", "use_when": "multi-step questions needing sources",
      "stages": ["plan", "research", "synthesize"],     // references stage ids
      "signal": { "any": [ {"lexical": ["compare", "vs", "research"]}, {"min_words": 8} ] },
      "threshold": 0.5 }
  ],
  "skills": [ /* {id, when, instructions, tools, disclosure, files} */ ],
  "weights": { "prior_research": 0.2, "edge_classify__plan": 0.4 },   // sparse overlay (§4)
  "budgets": { "context_tokens": 8000, "layer": {"2": 1200, "4": 3000} },
  "pinned_lobes": ["cite", "filter"]
}
```

A JSON Schema (`preact.spec.schema.json`) ships alongside; validate before loading.

---

## 2. The activation algorithm (port this exactly)

Per turn, given the spec and a `context` dict of free signals (see §3):

**A. Recognize the flow (intent).** For each flow, evaluate its `signal` over `context` (plus
its `weights` overlay). The flow with the highest score `≥ threshold` is selected; if none clears
threshold, the turn is *emergent* (the activated lobe set is the answer).

**B. Activate lobes.** For each lobe `j`, in `(layer, order)` order:

```
a_j = prior_j
    + Σ_k  w_k · signal_k(context)                  # the lobe's own declarative signal terms
    + Σ_i  edge_{i→j} · a_i                          # ONLY over upstream lobes that ACTIVATED
    + Σ    flow_bias_{flow→j} · flow_score           # bias from the recognized flow
activated_j  ⇔  pinned_j  OR  a_j ≥ max(threshold_j, weights["min_"+j])
```

Cascade rules (all four are load-bearing):
1. **Pinned bypass** — `pinned` lobes (`cite`, `filter`) activate regardless of threshold.
2. **Per-lobe threshold** — below it, the lobe does not fire, writes nothing, excites nothing.
3. **No speculative cascade** — only lobes that *activated* contribute to downstream `edge` sums.
4. **Forward DAG only** — every `edge`/`excites` must target a strictly later `(layer, order)`;
   reject specs that violate this (`validate_network`).

**C. Resolve the flow's stages.** Expand the selected flow's `stages` (id references) against the
stage table; apply per-bot customization from `weights`
(`flow_<flow>__stage_<stage>__disable` / `__lobe_<id>__add|remove`). Each surviving stage's
`signal` gates whether it runs.

**D. Build attention / budget.** For each stage, gather the `ContextNode`s its lobe slice
produces, then select under the token budget: **L1** lexical overlap with the query (free) →
**L2** semantic similarity if an `Embed` seam is present (else skip) → **L3** greedy fill under
`budgets`. Pinned/high-threshold nodes survive trimming.

Steps A–D are **pure and free** — no network, no model. They are the conformance surface.

---

## 3. Declarative signal expressions

`signal` is a small JSON expression evaluated against `context` → a float in `[0, 1]`. This keeps
activation host-language-free. Minimal grammar:

```jsonc
{"const": 1.0}                          // constant
{"flag": "is_question"}                 // context[flag] truthy → 1.0 else 0.0
{"lexical": ["compare", "vs"]}          // any term present in the query → 1.0
{"min_words": 8}                        // query word count ≥ n → 1.0
{"regex": "\\?$"}                       // query matches → 1.0
{"all": [<expr>, …]}                    // min() of children (AND)
{"any": [<expr>, …]}                    // max() of children (OR)
{"not": <expr>}                         // 1 - child
{"scale": [<expr>, 0.6]}                // child * weight
{"sum": [<expr>, …]}                    // clamped Σ
```

`context` is the **free signal substrate** the host computes deterministically before activation:
e.g. `query`, `is_question`, `word_count`, `has_history`, `route` (after classify writes it),
`fired_prompt`, `has_tasks`, `prev_flow`, plus any custom flags. The host owns lexical/structural
feature extraction (the "perception" layer); it must be deterministic.

Hosts MAY add an optional semantic activation path keyed on `use_when` (embed `use_when` vs the
query) — but it must be a *separate, declared* term so the free core stays reproducible.

---

## 4. Weight overlay (sparse, namespaced)

Customization is a flat string→float dict merged onto the spec at run time:

| key | effect |
|---|---|
| `prior_<lobe>` | base activation |
| `min_<lobe>` | per-lobe threshold |
| `w_<signal>` | weight of a named signal term |
| `edge_<src>__<dst>` | inter-lobe excitation |
| `flow_<flow>__<lobe>` | flow→lobe bias |
| `flow_disable_<flow>` | turn a flow off |
| `flow_<flow>__stage_<stage>__disable` | drop a stage from a flow |
| `flow_<flow>__stage_<stage>__lobe_<id>__add|remove` | mutate a stage's lobe slice |
| `budget_<layer>` / `context_tokens` | budgets |

Negative weights may **not** target a `pinned_lobes` member (ground-or-refuse invariant) — reject
such specs.

---

## 5. I/O seams (implement natively)

Everything outside the core is a small async interface. Ship in-memory defaults; add
network-backed adapters as needed.

```text
LlmCall(stage, system, messages, max_tokens, temperature?, tools?) -> Message{content, stop_reason, usage}
        # one model call; provider client owns streaming + retries + usage
ToolRuntime.get_tool_specs() -> [ToolSpec]      # JSON-schema tool definitions
ToolRuntime.call_tool(name, input, retrieved_chunks, already_read) -> str
Embed(texts) -> [vector]                         # OPTIONAL; absent ⇒ L2 attention skipped
SessionStore.load/append/compact                 # persisted conversation state (+ injected context)
MemoryStore.read/write/search                    # durable scoped memory (the `memory` tool)
Workspace.read/write/list/edit                   # OPTIONAL; a virtual FS for artifacts/documents
Queue.enqueue/consume · EventSink.publish        # serving (optional)
```

The **engine loop** to port (control flow only — it calls the core + the seams):

```
load session (seams; carries history + injected context) → build context dict (free features)
recognize flow + activate lobes + resolve stages + build attention (CORE)
for each stage:
    compose its system prompt from the stage's lobe slice
    loop == "single" → one LlmCall
    loop == "agentic" → ReAct tool_loop: LlmCall → run tool_use blocks → repeat (funnel observations)
    loop == "map"   → fan out over a scratchpad key
    (metacognition may trim/skip/retry — never cite/filter)
run cite + filter (pinned) → ground-or-refuse → assemble FinalEnvelope
persist session (+ memory writes) (seams) ; emit events throughout
```

---

## 6. Wire contracts (cross-language stable)

Three JSON shapes are the interop surface — keep them identical across ports:

- **Spec JSON** (§1) — `preact.spec.json` + its JSON Schema.
- **Event JSON** — each `AgentEvent` (`{type, trace_id, ts, …}`): `run_start`, `path_resolved`,
  `stage_start`, `text_delta`, `tool_call`, `tool_result`, `citation`, `meta_action`,
  `stage_end`, `final`. (This is the SSE/pub-sub payload.)
- **Tool spec JSON** — Anthropic-compatible `{name, description, input_schema}`.

A Python producer and a Rust consumer (or vice-versa) interoperate over these without sharing code.

---

## 7. Conformance

A port is correct when, for a shared corpus of `(spec, context)` cases, it reproduces the Python
reference's:

- recognized flow + score,
- activated lobe set + per-lobe activation values,
- resolved stage sequence,
- attention selection (given the same — or no — embeddings).

Ship these as golden fixtures (`conformance/*.json`: input `spec`+`context`, expected
recognition/activation/flow). The deterministic core makes this exact; the I/O seams are mocked.
This is the same discipline the Python suite already uses (the lobe/flow parity matrices) — lift
it to a language-agnostic fixture set.

---

## Why it ports cleanly

- The hard part of an agent framework — *what to think about and how to progress* — is a pure
  function here, fully specified by data + this algorithm.
- The messy part — models, tools, storage, queues — is behind narrow async interfaces every
  language already has libraries for.
- The three wire contracts let polyglot deployments share specs, stream events, and call tools
  without sharing a runtime.

See [`api.md`](./api.md) for the Python surface and [`preact.md`](./preact.md) for the model.
