# Context memory — scoped durable facts

How the bot remembers and recalls **durable facts** across turns: meeting
decisions, team conventions, on-call owners, a user's preferences. This is the
`context_entries` store fronted by one model-facing `memory` tool and a per-turn
`## Memory` index. It is the durable-fact half of the engine; the volatile,
recompute-every-turn half is **context variables** (see the last section — the
two are easy to confuse).

> **One tool, scope = durability.** There is a SINGLE `memory` tool
> (`MemoryToolRuntime`, `agent_core/tool_runtime.py`). Its `scope` is the
> durability axis: `turn` is **ephemeral working memory (RAM)** for the current
> reasoning turn — in-process, zero-latency, always available even without the
> `context_management` skill, backed by the turn `Scratchpad`
> (`agent_core/scratchpad.py`); it never touches `context_entries`. The four
> durable scopes below (`conversation`/`channel`/`user`/`bot`) persist across
> turns and only appear in the tool when `context_management` is active. This
> replaced the former separate `scratchpad.*` tools — one mental model now:
> *memory, scoped from `turn` (RAM) to `bot` (global).*

The recall side is the `memory_recall` lobe on the OY axis
(`agent_core/lobes/memory/memory_recall.py`); see `architecture.md`.

## The four scopes

Every fact is filed under one scope, broad → specific:

| scope | who shares it | `scope_ref` | example |
|---|---|---|---|
| `bot` | everyone, everywhere | `""` (global) | office holiday schedule (admin-pinned) |
| `user` | one person, any channel | mezon user id | "write my code reviews in Vietnamese" |
| `channel` | everyone in one channel | channel matcher | "deploy freeze until Monday" |
| `conversation` | one thread only | conversation id | a one-off demo passcode |

**`channel` is the common case.** Most real usage is a team talking in a shared
channel — project status, owners, conventions, announcements. The portal admin
editor and `benchmarks/contextbench` both default to / lean on channel scope.

The per-turn index renders scopes in `bot → user → channel → conversation`
order (broad → specific) so the most specific fact sits nearest the question and
wins on conflict (`INDEX_SCOPE_ORDER` in
`apps/backend/app/services/context_entries.py`). A fact is uniquely identified by
`(tenant_id, bot_id, scope, scope_ref, key)` — the `uq_context_entry`
constraint — so writes are **update-in-place** (same key overwrites; never a
`_v2` near-duplicate).

## `scope_ref` is resolved server-side — never by the model

The model picks a *scope* (`save` with `scope: "channel"`); it never supplies the
*ref*. The harness resolves `scope_ref` from the turn's injected identity:

```
bot          → ""                       (global)
user         → ctx.user_id
channel      → ctx.channel_id
conversation → ctx.conversation_id
```

This is the isolation guarantee. A turn can only read/write the refs for *its
own* identity, so user A can never see user B's facts and channel X's facts never
leak into channel Y. The MCP contract strips any caller-supplied identity field
(`tenant_id/bot_id/user_id/channel_id/conversation_id/scope_ref`) before the
request is built (`packages/arag-core/arag_core/mcp/context_tools.py`); a model
that invents an id is following the skill badly, and contextbench's verdict
requires zero such leaks. An empty ref for a non-bot scope is refused outright
("'channel' scope is unavailable here").

## Hybrid design: always-on index + one tool

**The `## Memory` index** is injected into the system prompt every turn:

```
## Memory
- [bot] office_holiday — Lịch nghỉ lễ: "Nghỉ 30/4 đến 2/5."
- [user] review_language — Preferred review language: "Vietnamese"
- [channel] deploy_freeze — Deploy freeze: "until Monday June 15"
```

It is capped per scope (`INDEX_MAX_PER_SCOPE = 12`), newest-first, admin-pinned
facts first (authoritative), with values **inlined under a ~1200-char budget**
(`INDEX_VALUE_BUDGET_CHARS`) so most recalls need **zero tool calls** — the
answer is already in the prompt. Past the budget, entries degrade to hint-only
with a "recall to read" note.

**The `memory` tool** is the single model-facing surface:

```jsonc
memory { "action": "save" | "forget" | "recall",
         "scope": "bot" | "user" | "channel" | "conversation",
         "key": "snake_case_id", "value": <any>,
         "description": "short recall hint", "ttl_days": 7 }
```

`save` upserts in place; `forget` hard-deletes; `recall` reads one key or lists
the visible entries (only needed for truncated/over-budget facts). `ttl_days` is
optional expiry for stale-prone facts (a freeze, an outage). Every successful
`save`/`forget` appends a deterministic chat footer (`• Đã ghi nhớ: key (kênh)`)
and a `FinalEnvelope.memory_updates` entry. The retired `context.*` tool names
remain callable aliases but are never offered.

## Per-turn injection path

```
interpreter._load_context_index()         agent_core/interpreter.py
  builds visible scopes from job identity  [{bot,""},{user,uid},{channel,cid},{conversation,convid}]
  → POST /v1/internal/context/index        apps/backend/app/routers/context_internal.py
      → svc.build_index(scopes=...)        list_entries → select_index_entries → render_index
  ← rendered block (+ structured rows when context_strategy ∈ {adaptive, llm})
```

When `context_strategy` is `adaptive`/`llm`, the index also returns the post-cap
**structured rows**, and the adaptive context builder scores each memory node
(lexical + semantic + budget) with per-scope boosts
(`scope_conversation > channel > user > bot` in
`agent_core/context_builder.py`) instead of dumping the whole rendered block.

## Admin management surface

The bot writes its own memory, but admins can also **pin facts by hand** from
the bot Context page (`/$workspace/bots/$id/context`), in **any scope**
(channel-first):

- API: `POST /v1/bots/{id}/context-entries` with `{scope, scope_ref, key,
  description, value, expires_at?}` (`apps/backend/app/routers/context_entries.py`).
  Pins are written with `updated_by="admin"` and render first in the index.
- Validation (`ContextEntryCreate`, `apps/backend/app/schemas/context_entries.py`):
  `bot` ⇒ `scope_ref` must be empty; `user`/`channel`/`conversation` ⇒
  `scope_ref` required — same rule the model's contract enforces.
- Where the admin gets a `scope_ref`: the portal sources **channel** refs from
  `GET /v1/bots/{id}/channels` (the channel `matcher` *is* the channel
  scope_ref), **conversation** refs from `GET /v1/bots/{id}/conversations`, and
  **user** refs as free-text mezon user id (there is no member-name directory).
- Identity is immutable: `PATCH` edits only description/value/expiry; changing
  scope/scope_ref means delete + re-create.

The page also shows every remembered fact grouped by scope with its `scope_ref`
("Phạm vi" column) so an admin can see exactly which channel/user a fact lives
in, and purge wrong ones.

## Memory vs. context variables (don't conflate)

The Context page surfaces two different systems:

| | **context memory** (this doc) | **context variables** |
|---|---|---|
| store | `context_entries` (durable rows) | resolved live each turn |
| code | `app.services.context_entries` | `rag_core.context_resolvers` |
| scopes | bot / user / channel / **conversation** | turn / channel / user / bot / **tenant** |
| written by | the bot's `memory` tool + admin pins | admin selects *which* variables; the engine *resolves* them |
| examples | remembered decisions, prefs, owners | current datetime, live channel member list, bot identity |

Context **memory** is what the bot chose to *remember*; context **variables** are
authoritative facts the engine *recomputes* every turn (and must never be
invented or stale). They render as separate prompt blocks (`## Memory` vs.
`## Context`) and have separate benchmark modes in contextbench (`resolve`/
`write`/… vs. `presolve`/`system_resolve`).
