# agentbench — the live benchmark for the SDK

The one bench. **Deterministic datasets in, the REAL agent driven against a live provider, an HTML
report out.** No stubs, no FakeClient — it measures what a plain `PreactAgent(client=…)` actually does,
and it has been the engine of an improve loop: *push the agent to failure → redesign the SDK defaults →
re-bench → raise the bar.*

## Run (needs a provider token in the repo `.env`)

```bash
uv --directory packages/agent-sdk run python benchmarks/agentbench/run.py --live --report
# → benchmarks/agentbench/report.html  (the live mission trace, gitignored)
```

It is **not** in the deterministic CI ladder (it calls a real provider). The code safety net is the
unit suite (`benchmarks/ci-free-gates.sh` runs `pytest`; the memory/efficiency behaviors are locked in
`tests/test_default_efficiency.py`, `tests/test_universal_memory*.py`, `tests/test_semantic_recall.py`).

## What it runs

One long **integrated mission** in a single conversation, using every capacity at once, then focused
**hard cases** at the edge. Inputs come from the committed deterministic datasets (`dataset/`: a messy
1500-turn ops channel + ground-truth facts/queries, a long migration instruction, a recall corpus).

| behavior | what it pushes |
|---|---|
| `mission.memorized` | ingest many channel facts **amid chatter** → offload them (≥ 22), ignore noise |
| `mission.recall_current_supersession` | a fact restated **9×** → answer the LATEST value |
| `mission.no_double_greeting` | a fresh chat then a follow-up → the reply **continues**, never greeting twice in a row (the response stage / reply flow) |
| `mission.distractor_entity` | the right owner among many projects' owners (entity-specific) |
| `mission.needle_recall` | a fact stated once → found across the conversation |
| `mission.synthesize_from_memory` | recall all 5 agreed migration decisions into a checklist |
| `mission.cross_session_recall` | recall a durable fact in a **new conversation** |
| `hard.bounded_context` | an **18-call** tool loop → context tail stays bounded (funnel) |
| `hard.recall_at_scale` | a needle among **120** facts |

Scored from the real `probe()` trace (tool calls, answer, `funnel_obs_chars`, memory store) with
**deterministic** distinctive-token matching (the LLM judge was unreliable, so it was dropped).

## The loop — what the bench found, and how the SDK was redesigned

Every change below was forced by a live failure and re-verified live; the unit safety net stayed green
throughout (each behind a rollback flag).

1. **The default had no memory, no bounded context.** → `PreactAgent` defaults flipped to
   `funnel=True` + `universal_memory=True`, with a **memory directive** injected as the engine's
   `system_addendum` (the user's `instructions`/spec stay pristine).
2. **The agent said "I don't have that" for things it stored.** → the **always-on memory index** is now
   injected into the prompt each turn (`render_index` via `engine._compose_system_segmented`); the agent
   sees a `## Memory` menu of what it knows.
3. **At scale the menu showed the newest, not the relevant.** → `render_index` was re-sorting by
   recency, discarding query relevance — **fixed** (a real bug).
4. **Facts noted without an explicit scope evaporated next turn.** → `note` is now **durable by
   default** (`conversation` scope), not `turn`/flash.
5. **Memorize was model-dependent (≈16/24).** → a native **`establish`** step
   (`agent_sdk/memory/establish.py`) auto-offloads fact-shaped statements every turn (→ ~39, ignoring
   chatter), **topic-keyed so an updated fact consolidates** over the old one (latest wins).
6. **A short tool loop never bounded (compaction only every 24 hops).** → the default funnel now carries
   a **token budget** (`working_set_budget`), so context compacts on a threshold at any hop count.

The result: a plain `PreactAgent(client=…)` now natively **memorizes, recalls (current value across
supersession, needles, cross-session, at scale), plans, synthesizes from memory, and keeps context
bounded** — no configuration. Opt out with `funnel=False, universal_memory=False, auto_establish=False`.

## Datasets (deterministic, committed)

`dataset/gen_dataset.py` (recall corpus) and `dataset/gen_channel.py` (the messy channel + long
instruction) regenerate the committed JSONL source; the bench reads it and drives the live agent.
