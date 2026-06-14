# Shared Context

> One handle, every component, every scope. A lobe, a tool, and a skill all reach the *same* state —
> from the turn in front of them out to what the bot knows — through a single object.

> **Status: direction.** Parts are live today; the rest is designed-but-not-shipped. See
> *Rollout* below for the live vs. to-build split.

## The problem

An agent's state today lives in many places, and every kind of component reaches it a different way:

| State | Where it lives | Lifetime | Who reaches it, and how |
|---|---|---|---|
| working notes | `Scratchpad` | turn (RAM) | tools, via the `current_turn()` contextvar |
| evidence pool | `retrieved_chunks` / `already_read` on `TurnContext` | turn | KB tools, via the two args threaded into `call_tool` |
| lobe handoff | `TurnContext.lobe_outputs` (untyped dict) | turn | lobes, by reading the dict |
| who / where | `TurnContext.identity` / `.channel` | turn | lobes, off the context; tools, via `current_turn()` |
| the conversation | `SessionState` (history/summary/facts) | conversation | the engine; lobes, via `session_memory` |
| durable facts | `Memory` (scoped key→value, async) **and** the universal `MemoryStore` (handle/digest, sync) | conversation · channel · user · bot | the model, via the `memory` tool; lobes, via prefetched `memory_items` |

So the same question — *"what do we know about this user?"*, *"what did the last tool find?"*, *"what did
the planner decide?"* — has a different answer surface depending on whether you're a lobe, a tool, or a
skill. There is no single place to stand. A tool can't see what a lobe decided without reverse-engineering
`lobe_outputs`; a skill can't read durable user facts without being handed a `Memory`; identity is a
read-only bag on the turn context but invisible to a skill body.

**Shared Context** removes that fragmentation: it is one object that *every* component holds, exposing
*all* of that state through *one* scoped interface — from the turn, out through the conversation, the
channel, the user, to the bot.

## What it is (and is not)

Shared Context is an **access facade and a single seam** — not a new store and not a new value model.

- **[Universal Memory](./06-universal-memory.md)** answers *where state lives and how it's valued/selected*:
  the entry shape, the durability scopes, the two tiers (flash/long-term), the CDS-budgeted selection.
  It is the **backend**.
- **Shared Context** answers *how every component reaches that state through one handle*: the scoped
  read/write interface, the ambient read-only turn facts (who/where/the conversation/the evidence), and
  the one seam (`current_context()`) that makes a lobe, a tool, and a skill share the *same* view. It is
  the **access surface** over Universal Memory + the turn's ambient state.

It is a thin **router**, not a cache: reads and writes pass straight through to the backing stores, so
there is exactly one source of truth and no divergence to reconcile.

## The scope ladder

The spine of Shared Context is the durability ladder Universal Memory already defines — narrowest
(shortest-lived, most local) to widest (most durable, most shared):

```txt
turn  ⊂  conversation  ⊂  channel  ⊂  user  ⊂  bot
RAM      this thread      this room   this person  every conversation
dropped  persists across turns / sessions ─────────────────▶  knows-everywhere
```

* **turn** — working scratch: sub-goals, the plan, the detected language, intermediate findings. RAM,
  dropped at turn end (backed by `Scratchpad` / Universal Memory flash). This is where a component
  *thinks*.
* **conversation · channel · user · bot** — durable knowledge: kept facts, decisions, profiles, rules
  (backed by `Memory` / Universal Memory long-term). This is what the agent *knows*.

A component asks the context for state *at a scope*; the context routes to the right backend. The model
never picks a backend — it picks a durability.

## One interface

```python
# scoped state — turn (RAM) out to bot (durable)
await ctx.get(key, scope=Scope.USER, default=None)   # read one
await ctx.set(key, value, scope=Scope.USER)          # write one
await ctx.delete(key, scope=Scope.TURN)              # drop one
await ctx.search(query, scope=Scope.BOT, k=5)        # free-text find

# ambient, read-only — the shape of the turn the component is inside
ctx.query                # the user's input this turn
ctx.identity             # who: principal / user / tenant (host-provided, never model-forged)
ctx.channel              # where: room / workspace
ctx.session              # the conversation: history, summary, facts
ctx.evidence             # what's been retrieved this turn (the shared evidence channel)
ctx.scratchpad           # the turn's RAM, for direct sync use inside a hot loop
ctx.path / ctx.active_lobes / ctx.stage_id   # the turn's shape, for components that adapt to it
```

The scoped four (`get`/`set`/`delete`/`search`) are the unification: they subsume the `scratchpad.*`
tools (at `scope=turn`), the `memory` tool (at the durable scopes), and the prefetched `memory_items`
(now just `ctx.search`/`ctx.get` at a scope). The ambient read-only properties expose the turn facts
that were previously scattered across `TurnContext` fields and the `current_turn()` seam.

## One seam — reached three ways, same object

The whole point is that the three component kinds end up holding the *same* `AgentContext`:

```txt
                     ┌─────────────────────────┐
   lobe   ──ctx──▶   │                         │
   tool   ──ctx──▶   │     AgentContext        │ ──▶ turn-scope  → Scratchpad / flash
   skill  ──ctx──▶   │  (one router, one view) │ ──▶ durable     → Memory / long-term
                     │                         │ ──▶ ambient     → identity·channel·session·evidence
                     └─────────────────────────┘
```

* **Lobes** receive it on the turn — the `TurnContext.context` field (the natural home for the today-unused
  `blackboard` slot). A lobe reads `ctx.context.identity` instead of `ctx.identity`, and writes a decision
  with `await ctx.context.set("plan", …, scope=Scope.TURN)` instead of poking `lobe_outputs`.
* **Tools** reach it through the `current_context()` contextvar — the same pattern as today's
  `current_turn()`, set by the engine for the duration of the turn. A tool that wants the user's prefs
  calls `await current_context().get("ui_pref", scope=Scope.USER)` — no closure over a `Memory` instance,
  no reverse-engineering `lobe_outputs`.
* **Skills** are handed it as the runtime argument they already lack. A skill body that needs to know
  *who it's talking to* or *what the planner decided* reads it off the context instead of being blind to
  everything but its own SOP text.

Because all three resolve to one object backed by one set of stores, a value a lobe writes at
`scope=turn` is the value a tool reads two hops later, which is the value a skill sees when it activates.

## Invariants it must preserve

Shared Context is plumbing; it changes *access*, never *guarantees*. The five SDK invariants
(`CONTRIBUTING.md`) all hold:

1. **Leaf isolation.** The submodule imports only `agent_sdk` + stdlib. It composes `Scratchpad`,
   `Memory`, `SessionState` — it does not reach into the host.
2. **Byte-identical default.** The facade is **opt-in**. An agent that doesn't construct/thread it runs
   exactly as before; the existing `TurnContext` fields and `current_turn()` seam stay. Wiring it as the
   default turn handle is a *later, separately-validated* phase, not a big-bang switch.
3. **ACL is server-side; identity is never model-forged.** `ctx.identity` is read-only and
   host-provided. The model-facing scoped writes go through the same scope allowlist the `memory` tool
   already enforces (`Memory._check`) — a model can't write `scope=bot` unless the agent permits it.
4. **Multi-tenant by default.** The durable backends are already tenant-scoped; the context adds no
   cross-tenant path. Tenant rides on `ctx.identity`, and durable keys inherit it from the backend.
5. **Compression invariant.** Raw KB chunks do **not** become shared-bag state. The evidence channel
   (`ctx.evidence`) stays the dedicated, dedupe'd pool for retrieved chunks; only shaped state
   (decisions, facts, notes) crosses components via `get`/`set`. The context never promotes a raw chunk
   into the durable scopes.

## Why a router, not a copy

The context holds **references** to the live backends, not snapshots:

* `scope=turn` → the live `Scratchpad` (and Universal Memory flash) — so a write is visible to the next
  reader within the same turn, with no flush step.
* durable scopes → the live `Memory` — so a `set(scope=user)` is the same write the `memory` tool makes,
  and the same read a prefetch hook would surface next turn.

One source of truth means there's nothing to invalidate: the context can't drift from the stores because
it *is* the stores, addressed by scope.

## Rollout (incremental, on the existing substrate)

Like Universal Memory, this lands on machinery that already exists — it is a façade over `Scratchpad`,
`Memory`, `SessionState`, and the evidence channel, not a rewrite.

1. **Submodule + seam (this change).** `agent_sdk/context/` ships the `AgentContext` facade, the `Scope`
   enum, and the `current_context()` / `bind_context()` contextvar seam. Constructable from a
   `TurnContext` via `AgentContext.from_turn(...)`. Fully opt-in; nothing in the kernel changes; default
   path byte-identical. Gated by `tests/test_shared_context.py` (round-trip per scope, ambient reads,
   the seam, leaf isolation).
2. **Lobe field.** Populate `TurnContext.context` from the engine's per-turn assembly (reusing the unused
   `blackboard` slot), so lobes can read the unified handle. Still additive — the old fields remain.
3. **Tool seam.** Set `current_context()` alongside `current_turn()` for the turn's duration; teach the
   concrete tool runtimes to prefer it. The narrow `call_tool(retrieved_chunks, already_read)` signature
   stays (it's the evidence channel); richer state moves behind `current_context()`.
4. **Skill arg.** Thread the context into the skill runtime so a skill body can read identity / durable
   facts / turn decisions.
5. **Fold the seams.** Once components read through the context, `lobe_outputs`, the duplicated
   `identity`/`channel` fields, and the two-named memory surfaces can be presented *only* through the one
   handle — collapsing the table at the top of this doc into a single row.

Each step is independently validated and independently reversible; the default network stays
byte-identical until a step is proven and flipped.

## Related

* [Universal Memory](./06-universal-memory.md) — the state backend + value model this is the access surface for.
* [Scoped Memory](./06-universal-memory.md) — the durable-scope semantics (`conversation`/`channel`/`user`/`bot`).
* [Architecture](./01-architecture.md) — the OX/OY model the context threads through.
