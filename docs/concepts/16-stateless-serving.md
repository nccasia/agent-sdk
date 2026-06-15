# Stateless Serving

> One JSON holds the whole session. The process holds only config. So any worker serves any
> session, a restart loses nothing, and the worker is just a queue: *load → turn → offload*.

## Overview

A long-running agent accumulates state: the conversation, the rolling summary, the facts it
extracted, which skills are active, the metacognition bias for next turn, and its
[universal working memory](./06-universal-memory.md). The naive way to keep that is to pin one
agent object per conversation in RAM — which doesn't survive a restart, can't move between
replicas, and caps you at "sessions that fit in one process."

PreAct splits a running agent into two halves:

```txt
CONFIG  (immutable, shared)         STATE  (per-session, in one JSON)
  the network: lobes/stages/flows     history + summary + facts + context
  weights, budgets, plugins, tools    skills_in_use + meta_flow_bias
  the LLM client                      universal memory (the _long tier + offloaded bodies)
  → built once, reused for every      → SessionState.to_json() — load to reconstruct the
    session                              session instantly, save to offload it
```

Config is a pure function of the bot (see the spec in [`../api.md`](../api.md) — `agent.spec()` /
`agent_from_spec`); it never changes mid-conversation, so one built agent serves all sessions.
**State is one JSON snapshot.** Hold config in the process, carry state in the snapshot, and the
agent is *stateless across sessions*.

## The one snapshot

`SessionState.to_json()` is the complete per-session state — nothing else is needed to reconstruct
a conversation:

```jsonc
{
  "v": 1,                       // SNAPSHOT_VERSION — the schema stamp
  "history": [ {role, content, metadata}, … ],
  "summary": "…",               // rolling compaction summary
  "facts":   [ "…" ],           // extracted durable facts
  "context": [ "…" ],           // per-conversation injected context
  "skills_in_use":  ["task_management"],   // SOPs active across turns
  "meta_flow_bias": "research",            // metacognition's next-turn bias
  "memory": { "seq", "long": [ … ], "docs": { … } }   // universal working memory
}
```

It is **plain JSON** — store it anywhere (Redis, a row, a file), ship it over a queue, diff it,
log it. `from_json` reconstructs the state; the agent rebinds its working memory to it per turn.

## Two ways to run stateless

**1 — the easy pure API.** No `Session`, no store: hand in a snapshot, get back the result and the
next snapshot. You own where it lives.

```python
result, snapshot = await agent.run_snapshot("What changed in v2?", snapshot=prev)
# persist `snapshot` wherever; pass it back next turn — that's the whole protocol
```

**2 — a `SessionStore`.** Let a store round-trip it by id. Every built-in store
(`SessionStoreInMemory` / `SessionStoreRedis` / `SessionStoreSQL`) persists the **whole** snapshot
(memory included) via `save`; the agent loads on turn start and offloads on turn end:

```python
agent = PreactAgent(client=…, session=Session("conv-42", SessionStoreRedis(url)))
await agent.query("…")     # loads conv-42's JSON, runs, saves it back — automatically
```

Both paths run the same restore-on-load / capture-on-finalize cycle inside the turn. A store that
only implements `append` (host-owned durability) falls back to the legacy two-append path
untouched — so wiring a custom store is opt-in, not forced.

## The worker is a queue

That cycle is exactly a queue consumer. `AgentWorker` *is* the loop:

```txt
request ─▶ find session id ─▶ load the one JSON from the one store ─▶ run turn ─▶ respond ─▶ offload
```

```python
from agent_sdk.serve import AgentWorker, RedisQueue, RedisEventSink, RedisLock
from agent_sdk.stores import SessionStoreRedis

worker = AgentWorker(
    agent_factory=lambda: PreactAgent(client=…),  # a POOL — one agent per in-flight turn
    queue=RedisQueue(url), sink=RedisEventSink(url),
    store=SessionStoreRedis(url),                  # the ONE store
    session_lock=RedisLock(url), concurrency=16,
)
await worker.serve()
```

A `Job` carries only a `session_id` (a `Session` object isn't serializable); the worker binds it to
its store, runs, and offloads. There is no per-session object held anywhere — only the bounded
agent pool and the store. Scale is bounded by the store and the per-session lock, **not** by
process memory, so one pool serves thousands of sessions and any replica serves any session.

## Concurrency: the pool, not a shared agent

One agent runs **one turn at a time** — it rebinds its working memory to the turn's snapshot, so two
turns interleaving on the same instance would race on that store. The fix is structural: the
`agent_factory` builds a pool of `concurrency` agents and each in-flight turn checks one out
exclusively. Different sessions therefore never share a live working-memory store, and `flash`
memory (turn-scratch) is reset each turn regardless. A single shared `agent` (no factory) still
works — turns simply serialize through it.

> Per-session state never lives on the agent. It is loaded from the snapshot at the top of the turn
> and written back at the bottom; in between it lives on the turn's `SessionState`. The agent is a
> stateless executor of *(config, snapshot) → (reply, snapshot′)*.

## Extending the snapshot

The schema is built to grow. `to_json` stamps `SNAPSHOT_VERSION`; `from_json` **ignores unknown
keys and defaults missing ones**, so:

- an **older** snapshot loads into a **newer** SDK (missing fields default),
- a **newer** snapshot loads into an **older** SDK (extra fields ignored),
- adding a field is additive — no migration. Branch on `d.get("v")` in `from_json` only if a future
  change is genuinely breaking.

New per-session state belongs on `SessionState` (it then rides every store and `run_snapshot` for
free); new *config* belongs on the spec.

## What proves it

[`benchmarks/statelessbench`](../../benchmarks/statelessbench/) — free, deterministic (FakeClient),
in the CI gate. Its modes: `snapshot` (a fact survives a hop to a fresh agent), `store` (any store
round-trips the whole state, a new agent resumes), `worker` (jobs carry only a `session_id`; the
worker loads/runs/offloads and resumes by id), `isolation` (a pool runs N sessions concurrently with
no cross-session bleed), `spec` (config round-trips through JSON), `schema` (versioned + tolerant).

## Related

- [Universal Memory](./06-universal-memory.md) — the working-memory store that the snapshot's
  `memory` field carries; `to_json` / `restore` are its snapshot seam.
- [ReAct Context Management](./04-react-context-management.md) — compaction + the funnel that shape
  what ends up in `summary` / `memory`.
- [Architecture](./01-architecture.md) — the config half (the network) that stays in the process.
- [`../api.md`](../api.md) — `run_snapshot`, `SessionState`, `SessionStore.save`, `AgentWorker`.
- [`../building-a-harness.md`](../building-a-harness.md) — wiring a real queue + transport.

## Design principle

Split config from state; make state one plain-JSON snapshot that contains everything; make the
turn a pure *(config, snapshot) → (reply, snapshot′)*. Then serving is just a queue over a store,
the process is disposable, and the snapshot is the only thing you have to keep.
