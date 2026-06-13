# Tool Use at Scale

> The prompt holds the *gist*; the store holds the *detail*; a tool reads it back.

This is the first application of [Universal Memory](./06-universal-memory.md): a tool result is one
**kind** of memory entry. The substrate built here (dense digest + offloaded body + read-back tool,
selected by value) generalizes to every other kind (decisions, notes, temp files, context).

## Overview

An agent that does real work makes **hundreds to thousands of tool calls** in a turn — explore a
repo, triage an incident channel, transform a large document. Two things break a naive ReAct loop at
that scale:

1. **Accumulation.** Every tool observation appended whole means the prompt grows `O(hops)`. By a few
   hundred calls the window is full of spent observations the model is no longer acting on.
2. **Large single results.** One `read_file`/`grep`/`retrieve_kb` can return thousands of tokens. Even
   one such result, injected whole and carried forward, dominates the window.

The objective is the same as [ReAct Context Management](./04-react-context-management.md) — *useful
reasoning per token* — applied to the **tool-result lifecycle**. The principle:

```txt
Naive:   keep every raw observation in the prompt forever        → O(hops), floods on big results
Funnel:  keep the few you're acting on; summarize the rest into  → O(working set), detail on demand
         a dense digest; offload the bodies; read them back by a tool
```

A spent result does not vanish and does not linger whole. It becomes a **dense digest in the prompt**
(the gist — cheap, high-density) plus a **handle into an offloaded body** (the detail — out of the
prompt) that the model can **read back with a tool** when the digest is not enough.

This document covers:

* The tool-result lifecycle (ingest → act → demote → summarize → offload → read-back)
* Threshold-triggered split / summarize / merge (learned from Claude Code)
* The result store and the read-back tools (`read_result` / `grep_result` / `read_section`)
* Value-aware protection (high-CDS + errors stay full)
* Large tool catalogs (adaptive exposure)
* Benchmarking and metrics

## Implementation status

The per-hop and budget machinery already exists; this design adds the **summarize → offload →
read-back** loop on top of it. All of it is **opt-in behind `working_set_budget`** — with no budget
set the loop is byte-identical to today's recency-only funnel.

**Live today (default on under `funnel=True`):**

* **Per-hop observation tiering** — `agent_sdk/react/funnel.py::tier_observations`, wired at
  `engine.py:716–764`. The newest `keep_last_full` observations stay full; older *spent* ones demote to
  a one-line hint that names the tool, preserving the `tool_use` ⇄ `tool_result` id pairing (no orphan →
  no provider 400). Bounds the growth **rate**.
* **Value-aware demotion** — `score_observations` (CDS vs the stage goal) pins the highest-value
  observations full via `keep_full_ids`; `keep_errors_full` keeps error observations full (the model is
  mid-retry). Value, not age, decides what survives.
* **Compaction (deterministic)** — `funnel.py::compact_observations` eliminates spent-hint *pairs*
  beyond `working_set_max_spent` and folds them into ONE rolling summary; tail becomes O(working set).
  Today the fold uses a deterministic excerpt.
* **Adaptive tool exposure (large catalogs)** — `engine._select_tools` + `agent_sdk/selection.py` +
  `lobes/tools/tool_select.py`: with `tool_strategy="adaptive"`, only the budget's worth of
  relevance-scored tools enter the prompt; an essentials set is never dropped. Handles the *many tool
  definitions* axis.
* **DocWorkspace (pure module)** — `agent_sdk/react/docworkspace.py`: `offload`→`outline`→`grep`→
  `read_section`→`write_part`→`assemble`. The file-tool discipline for a single huge body — never
  resident whole.

**Added by this design (to build):**

* **Dense semantic digest** — fill the existing `tier_observations` / `compact_observations`
  `summarize=` seam with a real summarizer (`agent_sdk/react/summarize.py`): a free deterministic digest
  by default, and a pluggable **cheap-model split/summarize/merge** (the Claude Code pattern) for
  production. The boundary digest preserves paths/names/decisions/numbers and drops transient chatter.
* **Result store + handles** — `agent_sdk/react/result_store.py`: when an observation is offloaded, its
  full body moves to an in-process `ResultStore` under a handle (`result://<tool>/<hop>`); the digest
  *names the handle*. Large bodies route to `DocWorkspace`.
* **Read-back tools** — `agent_sdk/react/result_tool.py::ResultToolRuntime`: `read_result(handle)`,
  `grep_result(handle, pattern)`, `read_section(handle, section)`, auto-wired beside the `memory` tool
  and added to the essentials guard so adaptive selection never drops them.
* **Engine wiring** — at the compaction point (`engine.py:716–764`): offload spent bodies → summarize
  the older block into the boundary digest → keep the read tools exposed → recurse the boundary at
  deeper layers (keep the last N hops full).

**Deferred:**

* **Auto-offload on ingestion** — capping a *single* oversized result at the call boundary (route it
  straight to `DocWorkspace` and hand the model an outline) rather than after a hop. Today a huge single
  result is full for one hop, then funnels.
* **Cross-turn result persistence** — the result store is turn/conversation-scoped; persisting handles
  across turns (so a digest from an earlier turn is still re-fetchable) is a later hook.

## The tool-result lifecycle

A tool result moves through four states across the loop. The transitions are driven by the
working-set budget, not a fixed hop count.

```txt
  ┌── ingest ──┐   ┌── act ──┐   ┌─ demote ─┐   ┌──── summarize + offload ────┐
  │ full body  │ → │ full,   │ → │ one-line │ → │ folded into ONE dense digest │
  │ in the     │   │ newest  │   │ hint     │   │ boundary; body → ResultStore │
  │ message    │   │ N hops  │   │ (spent)  │   │ under handle result://...    │
  └────────────┘   └─────────┘   └──────────┘   └──────────────────────────────┘
        ▲                                                      │
        └──────────────── read_result(handle) ────────────────┘
                     (the model reads the detail back on demand)
```

1. **Ingest.** The tool runs; its result is appended whole as a `tool_result` block. The model sees the
   full body on the hop it's acting on.
2. **Act → demote.** After the hop, `tier_observations` keeps the newest `keep_last_full` full and
   demotes older spent observations to a hint (recency), except those pinned by CDS or carrying an error.
3. **Summarize + offload.** When `obs_tail_tokens(msgs) > working_set_budget`, `compact_observations`
   folds the spent block: the bodies are **offloaded to the `ResultStore`** under handles, and the
   block is replaced by ONE **dense digest boundary** (the summarizer's output) that names the handles.
4. **Read-back.** If the digest is insufficient, the model calls `read_result(handle)` (or `grep_result`
   / `read_section` for a huge body) — the detail re-enters context, one bounded slice at a time.

## Threshold split / summarize / merge

> Learned from Claude Code's context system (`minhlucvan/claude-code-wiki`, docs/04): compact on a
> **token threshold**, keep the last N messages, **summarize the older block with a cheap model** into
> one boundary, and let boundaries **recurse**.

Per-hop tiering is free but its hint tail is still `O(hops)`. The amortized step runs only when the
observation tail crosses the budget — so the summarizer cost is paid once per *block*, not per result:

```txt
when  obs_tail_tokens(msgs) > working_set_budget:

  split:      [ ...older spent observations... ] | [ last keep_last_full full ]
  summarize:  older block ──(cheap model, bounded output)──▶ one dense digest
  merge:      replace the older block with the digest boundary (+ handles)

  recurse:    digest boundaries older than `compact_layers` re-summarize together
              → nested digests; the tail stays ~flat regardless of hop count
```

The **digest** is what makes this density, not truncation. It preserves the decision-relevant content —
file paths, identifiers, numbers, decisions, open TODOs — and drops confirmations and chatter. The
boundary is **cache-stable**: it is appended once and not rewritten, so the provider prompt cache
survives across subsequent hops.

The summarizer is a pluggable callback (`summarize(name, args, body) -> str`):

* **`deterministic_digest`** (default, free) — a structured extract (tool, args, salient lines, numbers,
  paths). Runs in CI and the free benchmark; no model call.
* **`llm_digest(client, max_tokens=…)`** — the cheap-model split/summarize/merge with a
  preservation-first prompt. Selected by the `result_summarizer` budget for production.

## The result store and read-back

A demoted/compacted body is not lost — it is **offloaded, not deleted**.

* `ResultStore.offload(tool, args, body) -> handle` returns a stable handle `result://<tool>/<hop>` and
  keeps the body in-process (turn/conversation-scoped). A large body is parsed by `DocWorkspace` so it
  can be sliced.
* The digest boundary names the handle, e.g.
  `[digest] read_file(src/engine.py) → defines Engine.stream + _agentic loop; 412 lines · read_result('result://read_file/37') for full`.
* `ResultToolRuntime` exposes the read-back surface, composed beside the `memory` tool:

```txt
read_result(handle)               → the full offloaded body
grep_result(handle, pattern)      → matching lines (DocWorkspace.grep) — not the whole body
read_section(handle, section)     → one bounded slice (DocWorkspace.read_section)
```

These tools are **essentials**: the adaptive tool selector never drops them, so a digest is always
re-expandable. This is the "model can read them back if needed" guarantee — the opposite of a silent
drop.

## Value-aware protection

Not everything should funnel by age. Two signals keep a body full regardless of how old it is:

* **CDS pin** — `score_observations(msgs, goal=…, keep_top=working_set_keep)` scores each spent
  observation by `relevance(goal) / cost` and pins the top-k full via `keep_full_ids`. A goal-critical
  result stays full even as newer off-goal ones arrive.
* **Errors** — `keep_errors_full` keeps a failed tool result full; the model is mid-decision about
  retrying.

The pinned set is goal-scoped (stable within a stage → low cache thrash) and bounded by `keep_top`
(never a monotone union that pins everything).

## Large tool catalogs

The *many tool calls* axis above is independent of the *many tool definitions* axis. With hundreds of
tools, the schemas alone flood the prompt. That is handled by **adaptive exposure** (already live):

* `engine._select_tools(stage, query)` scores every tool spec by `score_relevance(query, name+desc) /
  est_tokens(name+desc)` and keeps only the budget's worth (`tool_budget_tokens`), routing the rest to a
  hint or dropping them — the same 3-tier idea as context nodes, applied to tools.
* An **essentials** set (`stage.tools | {"memory"} | result-tools | external MCP tools`) is never
  dropped.
* The selection is computed once per stage and cached across hops, so the tool block is cache-stable.

## Benchmarking

The `tool-use-bench` suite (`benchmarks/tool-use-bench/`) gates this, reusing the SDK bench harness
(`benchmarks/_shared`). Free, deterministic modes (deterministic summarizer, no network):

```txt
catalog     every tool spec (incl. read_result*) has a usable name/description/schema
scale       N = 100 / 1000 synthetic tool calls → observation tail is O(working set),
            no orphaned tool_use, idempotent; reports the tail slope + projection@10k
digest      the summarizer folds a block into a dense boundary that PRESERVES the needles
            (paths/decisions/numbers) and meets a compression-ratio floor
refetch     a demoted/compacted result is re-fetchable via read_result(handle);
            grep_result / read_section on a huge body return the right slice
pin         high-CDS + error results stay full across the scale loop (value beats recency)
selection   large catalog: adaptive exposure keeps relevant / drops irrelevant
guard       essentials (read_result, memory) are never dropped
```

`live` (opt-in, real provider) drives a long loop with the cheap-model summarizer and asserts the turn
answers, growth stays bounded, and the model **reads a detail back** when the digest is insufficient.
`verdict` composes them → READY / NOT_READY / UNMEASURED, with one consolidated `--report` page.

## Core metrics

```txt
Tail boundedness    obs_tail growth per hop (chars)        → near-flat under compaction
Projection@10k      extrapolated tail at 10,000 hops       → stays under budget
Digest fidelity     fraction of needles surviving the fold → ≥ floor (no decision lost)
Compression ratio   digest tokens / raw block tokens       → small (dense)
Refetch success     read_result returns the exact body     → 1.0
Catalog leanness    tools kept / tools available           → small, essentials always kept
Cache stability     stable-prefix bytes identical hop→hop  → unchanged (boundary appended once)
```

The discipline matches the rest of the suite: deterministic gates decide READY; the `live`/judge tier
is recorded but never rescues a red gate.

## Design principle

A long-running agent is not the one that remembers every tool result. It is the one that keeps the few
results it is acting on, distills the rest to a dense gist, and **knows how to read the detail back when
it needs it.**

## Related

* [ReAct Context Management](./04-react-context-management.md) — CDS, the 3-tier router, per-hop tiering,
  compaction (this doc applies them to the tool-result lifecycle; it does not redefine them).
* [Scoped Memory](./06-universal-memory.md) — durable cross-turn facts; the result store is the in-turn,
  re-fetchable analogue for tool bodies.
* [Task Execution Mode](./13-task-execution-mode.md) — long-rail execution that drives the high tool-call
  counts this design bounds.
* [Subagent Fan-out](./12-subagent-fanout.md) — applies this compression across a worker *boundary*: a
  subagent returns a memo, not its tool-result dump.
