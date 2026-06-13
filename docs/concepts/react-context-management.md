# ReAct Context Management

> Optimizing context, not maximizing context.

## Overview

Traditional agent systems often assume that larger context windows produce better reasoning.

In practice, this assumption breaks down.

As context grows, attention becomes diluted, reasoning paths become noisier, latency increases, costs rise, and prompt cache efficiency decreases.

The goal of `agent-core` is not to maximize information inside the prompt.

The goal is to maximize:

```txt
Useful reasoning per token.
```

This model is **PreAct**: context is exposed by value and re-tiered every hop, so the prompt *funnels* toward the answer instead of *accumulating* toward the context limit.

```txt
Vanilla ReAct:  append every observation  → context grows each hop
PreAct:   tier every node by value  → context funnels each hop
```

This document introduces the adaptive context management system used by the ReAct loop, including:

* Context selection
* Lobe-driven context assembly
* Context density optimization
* Context-aware reasoning
* Context exposure tiers (offload · hint+tool · inject-full)
* Memory offloading
* Tool-based information retrieval
* Benchmarking and optimization metrics

## Implementation status

PreAct is **implemented and on by default** (policy `react_context_strategy: "funnel"`; `"accumulate"` is the rollback, `REACT_CONTEXT` env is the fleet kill switch):

* **CDS + 3-tier router** — `agent_core/context_builder.py`: `route_tiers()` / `render_tiered()`, `context_density()` (CDS = relevance × utility / cost), `ContextNode.{utility,cds,tier}`, `utility_<kind>` weights.
* **Tier-2 for the OY axis** — the adaptive context builder surfaces budget-dropped-but-above-hint nodes as a "References (recall to read)" hint block instead of silently dropping them (`interpreter._build_adaptive_context` → the `references` region).
* **Per-hop observation tiering** — `agent_core/react/funnel.py::tier_observations` + the `retier` hook on `lobes/runtime.py::tool_loop`, wired into every agentic loop in the interpreter. Newest observation full, spent ones demote to a hint (tool_use_id pairing preserved), the stable prefix is byte-stable for cache.
* **Value-aware demotion (not recency-only)** — `tier_observations` overrides age with two value signals so an old-but-critical observation is not silently dropped the moment a newer one arrives: `keep_errors_full` (error observations stay full — the model is mid-retry; **live, default on**) and `keep_full_ids` (CDS-pinned observations stay full regardless of age; with `keep_last_full=0` a low-value *newest* observation can itself demote). The `hard` gate proves recency-only loses these and the value/error-aware core keeps them.
* **Compaction tier (bounds the integral)** — `funnel.py::compact_observations`. Funneling reduces the growth *rate* but the spent-hint tail is still O(hops); over hundreds of tool calls it floods. Compaction ELIMINATES spent-hint *pairs* older than the most recent `max_spent` (tool_use + tool_result removed together — no orphan, role alternation preserved via `_merge_consecutive`) and folds them into ONE bounded rolling summary (≤`summary_lines` recent digests + a running offloaded count); full bodies live in `memory`, re-fetchable. Tail becomes O(working set). The `saturation` gate measures it: over 150 hops, value-aware grows +315 c/hop (→ ~319k @1000 hops) while compaction stays +9 c/hop (→ ~12k) with the SLA constraint still full. **Function + bench-proven; not yet wired into the live `_funnel_retier`** (that is the `working_set` lobe step below).
* **Telemetry** — `trace.attention.tiers` (per-node tier/CDS/utility) + `flow_steps[*].metadata.funnel_obs_chars` (per-hop tail series).
* **Gates** — `attentionbench` modes `tiers` (router correctness, no silent drop), `funnel` (bounded marginal growth, pairing, idempotence, stable prefix), `hard` (data-driven adversarial: old-critical, memory-recall, mid-retry error, value-beats-recency, supersession, distractor traps, multi-piece synthesis, re-expansion), and `saturation` (long-run ceiling: value-aware unbounded vs compaction bounded) — all in `benchmarks/ci-free-gates.sh`.

* **DocWorkspace (heavy large-input/large-output)** — `funnel.py`'s sibling `react/docworkspace.py`. The `memory` tool stores one fact per key and recalls it WHOLE, which floods on a large pasted document (md→html, summarize-each, extract-all). DocWorkspace offloads the body and exposes the file-tool discipline a long-running coding agent uses: `offload`→outline (the `glob`/`ls` view), `grep` (matches, not the doc), `read_section` (one bounded slice — the only path the body enters context), `write_part`, `assemble` (long-form output). The `heavy` gate proves the body is never resident whole — peak working context is 2.7–3.8× smaller than the pasted doc while the output covers every part. **Pure module + bench-proven; the MCP tool wrapper exposing these ops to the model in the loop is the live-integration layer.**

* **Enriched conversation context** — `react/conversation.py`'s `ConversationProfile`: a living, per-turn distillation of the conversation (intent · salient entities · offloaded-artifact manifest · open obligations · established facts · recent tools), persisted across turns. It is the highest-density context there is — a few tokens that change many decisions — and exposes three views over one state: `signals()` (free flags for the lobe network), `render()` (a compact "conversation state" node the model reads), and `keep_anchors()`/`keep_tools()` (value-aware pins + intent-driven tool families). The `convo` gate measures the headline lift: **intent-driven tool keep = 1.0 recall vs 0.1 lexical** — keeping the channel/task tool families by recognized intent where name-matching (today's `tool_select`) drops them. It unifies the rest: the heavy-agent map-pin is `keep_anchors` over the artifact manifest; value-aware keep is `keep_anchors` over established facts. **Pure module + bench-proven.**

* **`enrich` stage — LIVE (opt-in via the `context_enrich` policy flag)** — `flows/stages/enrich.py` + `interpreter._enrich_profile`. The turn's opening recognition stage (before `classify`) builds the `ConversationProfile` and feeds its three views to the live loop: `signals()` → `_flow_signal_ctx`, `keep_tools()` → `_select_tools` (intent families kept as essentials), `keep_anchors()` → `_funnel_retier` (facts + artifact map pinned). Inert by default (network byte-identical at parity — `lobe-builder`/`diff` green). **Measured live: toolbench `relevance` tool_recall 0.562 → 1.0 (16/16) with `irrelevant_drop` held at 0.962** — full recall AND leanness. Metadata feeds signals; it never routes (no LLM judging the pipeline).

**Deferred:**
* **Cross-turn persistence + establishment hooks** — the live profile is currently rebuilt per turn from the query; persisting it in SessionMemory (so facts/obligations/artifacts accumulate across turns) and populating `facts` (on a resolved answer) / `artifacts` (on a DocWorkspace offload) are the remaining hooks. The `convo` gate already proves the accumulation logic; the live loop needs the write points.
* The **`tools/working_set` lobe** — sibling of `tools/tool_select` (which governs *which tools* enter the window); it would own the tool-loop tail live: a **CDS observation scorer** populating `keep_full_ids` from the goal/sub-goal, plus calling `compact_observations` in `_funnel_retier`. Today compaction + the value pin are bench-proven and caller-available, but the live loop only auto-keeps errors and does not compact — so production context is still O(hops).
* **DocWorkspace MCP tool wrapper** — expose `offload`/`outline`/`grep`/`read_section`/`write_part`/`assemble` as model-callable tools so the engine drives the heavy form live (today the capability + the `heavy` gate prove the mechanics, but the model cannot yet call them in a real turn).
* **Multi-intent = a complex query** — a turn bundling N independent questions should route to the existing `plan` decompose path (one aspect per question), not a separate mechanism; the `multiquery` gate measures the N=10 recall cliff (`recall_by_n`) that this would flatten.
* The metacognition tier-move actions (`trim/summarize/retrieve/expand_context`) — today's metacognition keeps `context_tight` → `adjust_lobe_slice`.
* A **real-LLM funnel-vs-accumulate A/B** (judge answer-equivalence) — the structural gates prove "smaller + nothing critical dropped"; only an exam tier proves "still answers correctly."

---

# The Problem

Most agent frameworks optimize for:

```txt
More context
```

The engine optimizes for:

```txt
Better context
```

Consider:

```txt
Question:
Should I use Go or Python for my enterprise agent runtime?
```

Context A:

```txt
20 pages of:
- Go tutorials
- Python tutorials
- Concurrency theory
- Operating systems
```

Context B:

```txt
User is a solo engineer.
User prefers simple deployment.
User values operational simplicity.
```

Context B contains significantly less information but provides significantly more reasoning value.

The objective is therefore:

```txt
Reduce reasoning entropy.
```

instead of:

```txt
Increase information volume.
```

---

# Context as Attention Allocation

Context is not merely knowledge.

Context is attention allocation.

Every token inserted into the prompt competes for finite model attention.

The context builder therefore performs a continuous optimization problem:

```txt
Limited Attention Budget
          ↓
Select Highest Value Context
          ↓
Guide Reasoning Trajectory
```

The engine actively attempts to shape the model's attention space toward the most relevant reasoning path.

---

# Context Architecture

The engine separates:

```txt
OY Context Axis
```

from

```txt
OX Flow Axis
```

Context is assembled independently of execution flow.

```txt
User Message
      ↓
Intent Recognition
      ↓
Lobe Activation
      ↓
Context Selection
      ↓
Budget Optimization
      ↓
Prompt Assembly
      ↓
Flow Execution
```

This separation allows context optimization without changing execution behavior.

---

# Lobes as Context Producers

A lobe is a context-producing unit.

Examples:

```txt
memory_recall
session_recall
task_state
skill_activate
research
synthesize
cite
filter
format
```

Each lobe contributes context independently.

```ts
type ContextNode = {
  id: string
  lobe: string
  content: string
  tokens: number
  score: number
}
```

The context builder combines nodes from activated lobes and selects the highest-value subset.

---

# Adaptive Context Building

Context assembly is performed dynamically on every ReAct loop iteration.

```txt
Collect Candidates
      ↓
Score Candidates
      ↓
Apply Budget
      ↓
Render Context
```

Not all information is promoted into the prompt.

Many facts remain externalized and are retrieved only when needed.

---

# Context Density

A core optimization target is:

```txt
Context Density
```

Definition:

```txt
Reasoning Value
───────────────
Prompt Tokens
```

High-density context:

```txt
Current task blocked by API rate limit.
```

Low-density context:

```txt
Entire API documentation.
```

The objective is to maximize density while minimizing token consumption.

---

# Attention Space Optimization

The model has limited attention.

Adding information increases:

```txt
Attention Competition
```

Excessive context often harms reasoning.

The engine therefore attempts to maximize:

```txt
Attention Focus
```

instead of:

```txt
Attention Coverage
```

A smaller focused prompt frequently outperforms a larger noisy prompt.

---

# Context Value Model

Each context node receives a score.

## Relevance

Measures:

```txt
How related is this information to the current turn?
```

Signals:

* semantic similarity
* lexical similarity
* intent match
* recency
* scope proximity

## Utility

Measures:

```txt
How useful is this information for solving the task?
```

Signals:

* constraint value
* decision value
* planning value
* citation value
* conflict resolution value

## Cost

Measures:

```txt
How expensive is this information?
```

Signals:

* token count
* cache invalidation impact
* retrieval cost

---

# Context Density Score

The primary optimization metric:

```txt
CDS
=
(Relevance × Utility)
─────────────────────
Cost
```

or

```txt
CDS
=
Thought Steering Value
──────────────────────
Tokens
```

Higher scores are better.

---

# Thought Steering

Not all relevant information improves reasoning.

The engine measures:

```txt
Thought Steering Value
```

Definition:

```txt
How much does this information push the model
toward the correct reasoning trajectory?
```

Examples:

High steering:

```txt
User is a solo engineer.
```

Low steering:

```txt
Go was created in 2009.
```

Both are factual.

Only one meaningfully changes the decision.

---

# Lobe Weight Optimization

Each lobe may be weighted.

Example:

```json
{
  "memory_recall": 1.0,
  "session_recall": 0.8,
  "research": 0.5,
  "skill_activate": 1.2
}
```

Weights influence:

```txt
Activation probability
Context budget allocation
Selection priority
```

without modifying execution flow.

---

# Context-Aware Reasoning

The engine should not assume all information belongs in the prompt.

Instead:

```txt
Prompt
Memory
Tools
```

form a unified reasoning surface.

The model should reason using:

* prompt context
* indexed memory
* external retrieval
* tool calls

rather than relying solely on prompt stuffing.

---

# Context Exposure Tiers

A context node is not simply *in* the prompt or *out* of it.

Early designs treated selection as binary:

```txt
selected   → full text injected
not selected → silently dropped
```

The failure mode of the binary model is the **silent drop**:

```txt
A useful fact loses the budget gate by a hair,
disappears entirely,
and the model never learns it existed —
so it cannot even choose to retrieve it.
```

The engine instead routes each candidate node into one of **three exposure tiers**, chosen by its value (CDS / thought-steering) against its cost:

```txt
Node Value  ×  Cost
         ↓
 high value, cheap      → INJECT FULL
 medium / uncertain /
 high value but large   → HINT + TOOL
 low / speculative      → OFFLOAD
```

## Tier 1 — Inject full

The node earns its tokens.

```txt
Prompt footprint:  full node text
Retrieval:         none needed — it is already here
Use when:          high steering value AND low cost
                   (high CDS — the fact changes the decision
                    and is small enough to pay for)
```

## Tier 2 — Hint + tool

The node is **referenced, not expanded**.

A cheap one-liner goes into the prompt (the node's `menu_hint`), paired with an exposed tool that can pull the full content on demand.

```txt
Prompt footprint:  one-line reference (menu_hint)
Retrieval:         model calls the pull tool if it decides
                   the detail is worth the round-trip
Use when:          value is medium or uncertain, OR the fact
                   is high-value but too large to inject whole
```

The point of Tier 2 is **discoverability**. The fact is externalized, but the model *knows it exists* and holds the affordance to fetch it — the opposite of the silent drop.

```txt
Hint:
  customer_profile (deployment preferences, team size)
        ↓  if the turn needs it
memory.recall("customer_profile")
```

## Tier 3 — Offload

The node contributes nothing to this prompt.

```txt
Prompt footprint:  none
Retrieval:         general tools only (search / retrieve_kb /
                   scratchpad), no per-node pointer
Use when:          value is low or purely speculative for this turn
```

Tier 3 is correct for large, low-steering sources (full documentation, history, research notes) — surfacing a pointer for every one would itself flood attention.

## Choosing the tier

The same value model that scores selection drives the tier:

```txt
CDS = (Relevance × Utility) / Cost
```

```txt
CDS ≥ inject_threshold        → Tier 1 (inject full)
hint_threshold ≤ CDS < inject → Tier 2 (hint + tool)
CDS < hint_threshold          → Tier 3 (offload)
```

Cost bends the boundary: a high-value node whose token cost is large is demoted from Tier 1 to Tier 2 — keep the pointer, pay for the body only if asked. Pinned and admin-authored nodes are floored at Tier 1.

## Cache and the tiers

The tiers map cleanly onto cache stability:

```txt
Tier 2 hints   → small, stable → cache-friendly, cheap to keep
Tier 1 bodies  → large, often turn-volatile → the real cost
```

Preferring Tier 2 for volatile-but-maybe-needed facts protects the prompt cache: the stable hint stays put while the expensive body is fetched out-of-band only when the turn actually demands it.

---

# Inter-Hop Context Adaptation

A turn is not one prompt.

It is a ReAct loop of hops:

```txt
think → act (tool) → observe → think → act → observe → ...
```

Context is reassembled every hop — the tier of a node is decided per *hop*, not pinned at turn start.

The naive loop **grows** context each hop: append every observation, keep everything. This is the silent-drop failure inverted — instead of losing facts, the prompt balloons with stale observations and dilutes attention exactly when the trajectory should be narrowing.

The objective across hops — the behavior the model is named for:

```txt
Context should FUNNEL, not ACCUMULATE.
```

## What changes between hops

Three forces re-shape the working set on each iteration:

```txt
1. New observation   — the last tool result enters as a candidate
2. Tier transitions  — nodes move between tiers as the sub-goal evolves
3. Budget re-focus   — attention concentrates as the sub-goal sharpens
```

## Tier transitions across hops

The hop is where tiers move:

```txt
Tier 2 hint  --(model calls the pull tool)-->  Tier 1 body
Tier 1 body  --(used, now spent)----------->  Tier 2 hint / Tier 3
Tier 3       --(sub-goal now needs it)------>  Tier 2 hint
```

A fact the model expanded on hop 2 need not stay a full body through hop 6. Once consumed, it demotes back to a pointer — freeing budget for the next sub-goal instead of riding along as dead weight.

## Observation tiering

The largest source of hop-over-hop bloat is raw tool output.

```txt
retrieve_kb returns 12 chunks (~4k tokens)
```

Injecting all 12 into every later hop is fatal to density. Instead the observation is **compressed before it crosses the next hop**:

```txt
raw chunks  →  memo (memo_schema_ref)  →  tiered like any node
```

Raw chunks never cross a stage boundary — only the memo does (the compression invariant). The raw source stays Tier 3, re-retrievable by reference.

So hop N+1 sees the **conclusion** of hop N's action, not its raw payload.

## Decay and eviction

As the trajectory moves, earlier observations lose relevance.

```txt
recency_rank decays
superseded hints drop
a sub-goal that closed evicts its working nodes
```

Eviction is not deletion. An evicted node falls to Tier 3 — still reachable by tool if the loop revisits that ground.

## Metacognition between hops

Between hops the metacognitive monitor reads the loop's state and regulates composition — the same observations/actions, now expressed as **tier moves**:

```txt
context_tight    → trim_context      (demote the weakest Tier 1 → Tier 2)
missing_memory   → retrieve_memory   (promote a Tier 2 hint → Tier 1 body)
context_overflow → summarize_context (collapse spent bodies)
low_confidence   → expand_context    (raise a Tier 2 → Tier 1)
```

Tier movement here is driven by the loop's own health, not by static scores alone.

## Cache across hops

Hops are **append-mostly** by design:

```txt
[ stable prefix: system · tool schemas · policy · stable memory ]   ← unchanged every hop
[ hop tail: new observation, tier moves ]                           ← the only churn
```

Keeping the prefix byte-stable across the whole turn preserves the provider prompt cache hop after hop; only the growing-then-funneling tail is uncached.

The combined effect:

```txt
Per hop, the prompt earns its tokens again —
new evidence in, spent evidence down a tier,
the stable prefix untouched.
```

---

# Reference Indexing

Reference indexing is the **Tier 2** mechanism for facts.

Facts should be referenced before they are expanded.

Example:

```txt
Memory:
- customer_profile
- project_constraints
- deployment_preferences
```

instead of:

```txt
Full customer profile
Full project history
Full deployment history
```

When detail is required:

```txt
memory.recall("customer_profile")
```

is executed.

This converts context into retrieval.

---

# Tool-Based Context Offloading

Offloading is the **Tier 3** mechanism for large sources.

A prompt should not become a database.

Large information sources remain external.

Examples:

```txt
Documentation
Knowledge Bases
Historical Conversations
Research Notes
Project Records
```

The engine stores references inside context and retrieves full content through tools when required.

Benefits:

* lower token usage
* lower latency
* higher cache reuse
* better attention focus

---

# Memory Offloading

Memory offloading is how a fact *enters* the tier system: write it once, then let the value model pick its tier on every later turn.

Many facts can be stored as memory.

Example:

```txt
User prefers Go.
```

becomes:

```txt
memory.save(
  key: user_language_preference,
  value: go
)
```

On a later turn the fact is tiered by its value to *that* turn:

```txt
unrelated turn   → Tier 3 (offload, no pointer)
maybe-relevant   → Tier 2 (hint: user_language_preference)
deciding the turn → Tier 1 (inject: "User prefers Go.")
```

A Tier 2 hint is promoted to a Tier 1 body — via `memory.recall` — only when full retrieval becomes necessary.

---

# Prompt Cache Optimization

Stable context should remain stable.

Recommended structure:

```txt
System Prompt
Tool Schemas
Bot Policies
Stable Memory
----------------
Dynamic Context
User Message
```

Only the dynamic section changes frequently.

This maximizes provider-side prompt cache efficiency.

---

# Benchmarking

Every context decision should be measurable.

The engine benchmarks:

* answer quality
* citation quality
* cost
* latency
* cache efficiency
* context density

---

# Core Metrics

## Context Density

```txt
Reasoning Value
───────────────
Tokens
```

## Context Efficiency

```txt
Useful Tokens
─────────────
Total Tokens
```

## Cache Hit Rate

```txt
Cached Tokens
─────────────
Total Tokens
```

## Cost Efficiency

```txt
Answer Quality
──────────────
Cost
```

## Runtime Efficiency

```txt
Answer Quality
──────────────
Latency
```

---

# Benchmark Categories

The engine should maintain benchmark suites for:

```txt
Simple Q&A
Memory Recall
RAG Retrieval
Research Tasks
Tool Workflows
Long Running Tasks
Task Continuations
Multi-Hop Reasoning
```

Each benchmark tracks:

```txt
Answer Correctness
Context Density
Cost
Latency
Tool Usage
Memory Usage
```

---

# Metacognitive Context Regulation

Metacognition monitors context quality.

Observations:

```txt
context_tight
low_confidence_path
empty_lobe_slice
context_overflow
missing_memory
```

Possible actions:

```txt
trim_context
summarize_context
retrieve_memory
expand_context
retry_step
```

Metacognition regulates context composition without directly replacing reasoning.

---

# Design Principle

The objective is not:

```txt
Maximum Context
```

The objective is:

```txt
Maximum Thought Steering
Maximum Context Density
Maximum Attention Focus
Maximum Reasoning Value
Per Token Spent
```

The best agent is not the agent with the largest prompt.

The best agent is the agent that spends attention wisely.

That is **PreAct**: every hop, the prompt earns its tokens again.
