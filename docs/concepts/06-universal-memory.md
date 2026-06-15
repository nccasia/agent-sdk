# Universal Memory

> Everything is memory. Context, decisions, notes, tool results, temp files — one model, one
> interface, one efficiency mechanism.

## Overview

An agent produces and consumes many kinds of information in a turn: the **context** it's given, the
**decisions** it reaches, the **notes** it jots, the **tool results** it fetches, the **temp files** it
writes. Today each lives in its own subsystem — context nodes, the funnel observation tail, durable
memory, the scratchpad, DocWorkspace, the conversation profile. They all solve the *same* problem:
**hold a lot of information without flooding the prompt.** And they all solve it the *same* way: keep a
dense gist in the prompt, offload the full body, read it back when needed.

Universal Memory unifies them. There is one entry type, one read/write interface, and one efficiency
rule:

```txt
Every piece of information the agent touches is a MEMORY ENTRY:
  a dense DIGEST (the gist — the only thing that goes in the prompt by default)
  + an offloaded BODY (the detail — out of the prompt, re-fetchable by handle)
  + a VALUE (relevance × utility / cost — governs whether it's injected, hinted, or offloaded)

The prompt at any moment is a value-budgeted SELECTION over memory — never the whole of it.
```

This is the same PreAct machinery from [ReAct Context Management](./04-react-context-management.md)
(CDS, the 3-tier router, per-hop tiering, compaction) — generalized from "context nodes" to **all
information kinds**. [Tool Use at Scale](./05-tool-use-at-scale.md) is one application (the `tool_result`
kind).

## Memory as a thinking space

Universal memory is not a filing cabinet the agent reads from — it is the **surface the agent thinks
on**. The per-hop tiered selection (the Tier-1 full bodies + the Tier-2 digests) *is* the agent's
**thinking palette**: the small, curated set of things in view right now. The large store behind it is
everything it *could* bring into view. Recall efficiency — the right few entries in the palette, the rest
one `read` away — is the whole game.

Two directions, both first-class:

* **Write to think.** The agent externalizes working state as entries: a `plan`, its `sub-goals`, a
  `hypothesis`, a `decision`, a `note`. Writing a thought to memory frees the prompt of it while keeping
  it recoverable — the way a person works a problem on paper. These entries are tiny and high-utility, so
  they ride in the palette cheaply and persist across hops.
* **Recall to think.** Selection pulls exactly the entries a step needs into the palette and lets the
  rest rest at a digest + handle. The agent does not re-derive what it already concluded — it recalls it.

So the loop is: *think → write the conclusion as an entry → the palette re-tiers → the next step sees a
dense, current surface.* The reasoning trajectory is recorded in memory as it goes, and the working
surface stays small no matter how long the agent thinks. This is what makes a 1000-step run coherent:
the agent is not holding 1000 steps in its head, it is holding a dense palette over a large, addressable
memory.

It also sharpens the value model: an entry's **utility** is *how much having it in the palette steers the
next decision*. A `decision`/`plan`/`obligation` steers hard — small, always in view; a raw `tool_result`
steers once, then drops to its digest. CDS becomes the answer to *"what should I be thinking about now?"*

## The entry

```txt
MemoryEntry
  handle     mem://<kind>/<scope>/<key>     stable id — how the body is read back
  kind       context | decision | note | tool_result | temp_file | fact | obligation | artifact
  scope      turn | conversation | channel | user | bot     (durability)
  digest     dense gist — what the prompt holds (string, small)
  body       full content — offloaded, never resident in the prompt by default
  # value (governs selection + tiering — the CDS model, applied to every kind)
  relevance  match to the current goal/query        utility  thought-steering weight of the kind
  cds        relevance × utility / cost             tier     1 inject-full · 2 digest+tool · 3 offload
  pinned     bypass the budget gate                 recency  newest-first ordering signal
  tokens     size of the body
  # provenance
  source     the tool / lobe / hop that produced it
  meta       kind-specific: {tool,args} · {path} · {decision_for} · {section span} · …
```

A `tool_result` is an entry whose body is the raw output and whose `meta` is `{tool, args}`. A
`temp_file` is an entry whose body is the file content and whose `meta` is `{path}`, sliceable. A
`decision` is a small high-utility entry the agent writes when it concludes something. The *kind* only
changes the digest template and the read affordances — the storage, valuation, and read-back are
identical.

## Two tiers: long-term and flash

Scope (durability) collapses into two operational tiers, because the agent treats durable knowledge and
working scratch very differently:

```txt
                LONG-TERM (durable)                  FLASH / short-term (working)
  holds         what the agent KNOWS:                where the agent THINKS:
                user facts, bot facts,               tool_results, reasoning temps
                conversation facts, kept decisions   (notes · sub-goals · hypotheses) · intermediate
  scope         conversation · channel · user · bot  turn
  lifetime      persists across turns / sessions     discarded at turn end (unless promoted)
  backend       durable store, consolidated+indexed  in-process RAM (cheap, fast)
  in palette    always-on index (capped, newest,     funneled tail: newest full, spent → digest +
                high-utility — a few tokens)          offloaded, re-fetchable by handle
  efficiency    dedup/consolidate on write;          densify + offload on a token threshold;
                small stable index (cache-friendly)   drop at turn end; promote what proves durable
```

* **Flash is where the agent thinks.** Tool results and reasoning temps live here — cheap, ephemeral; the
  funnel keeps the working surface dense, and it evaporates at turn end, except what gets **promoted**.
* **Long-term is what the agent knows.** A flash entry that proves durable — an established fact, a
  concluded decision — is *promoted* (write-back) into long-term, consolidated against what's already
  there, and from then on rides in the palette via the always-on index.
* **Recall draws from both** into the thinking palette: long-term facts (stable, high-utility, a few
  tokens) + the current flash working set (the trajectory in progress).

```txt
  flash.remember(tool_result | note | sub_goal)  ──funnel──▶  digest + handle   (this turn only)
                             │ proves durable (a fact, a decision)
                             ▼ promote (write-back + consolidate)
  long_term.remember(fact | decision)            ──index───▶  always-on, cross-turn
```

`MemoryEntry.scope` selects the tier — `turn` → flash; everything else → long-term. The *kind* says what
it is; the *scope* says how long it lives. Both tiers share the one entry shape, the one interface, and
the one value model — they differ only in backend (RAM vs durable) and lifetime (dropped vs promoted).

## One interface

```txt
remember(kind, content, *, scope="turn", key=None, digest=None, meta=None) -> handle
recall(query=None, *, handle=None, kind=None, scope=None, full=False)      -> entries | body
forget(handle) -> bool
```

* `remember` stores the body and attaches a digest (deterministic by default; a cheap-model summary for
  large/spent entries — see *Densification*). Returns the handle.
* `recall(query=…)` searches the digest index (cheap, in-prompt-shaped). `recall(handle=…, full=True)`
  reads the body back (the detail, on demand). `recall(kind=…, scope=…)` lists.
* `forget` drops an entry.

This one interface subsumes today's separate surfaces:

| Today | Is just | 
|---|---|
| `Scratchpad` (turn RAM) | universal memory at `scope=turn` |
| `Memory` (durable facts) | universal memory at `scope=conversation/channel/user/bot` |
| the funnel observation tail | entries of `kind=tool_result`, `scope=turn` |
| `DocWorkspace` | the large-body **slicing backend** for any kind |
| `ContextNode` / `build_attention` | entries of `kind=context` |
| `ConversationProfile` facts/obligations | entries of `kind=fact/obligation/decision` |

## The prompt is a selection over memory

The engine never injects all of memory. Each turn (and each hop), it **selects** the highest-value
entries under a token budget and **renders them tiered** — exactly `route_tiers` / `render_tiered`, now
over every kind:

```txt
                 ┌──────────────── universal memory ────────────────┐
   recall(goal)  │ context · decisions · notes · tool_results ·     │
   ───────────▶  │ temp_files · facts · obligations · artifacts     │
                 └──────────────────────────────────────────────────┘
                                     │ score by CDS, budget
                                     ▼
   Tier 1  inject full      the few highest-CDS entries — full body in the prompt
   Tier 2  digest + tool    the gist + a read-back handle (discoverable, re-fetchable)
   Tier 3  offload          handle only in the index (nothing in the prompt)
```

* **Per-hop** the selection re-tiers (a spent tool_result drops to its digest; a now-relevant note rises
  to full) — the funnel, applied to memory.
* **At a token threshold** the spent block is densified and offloaded — compaction, applied to memory.
* **Cache-stable**: the digest index is append-mostly, so the provider prompt cache survives.

## Densification (digest, learned from Claude Code)

A body becomes a digest by **summarization, not truncation**. Two summarizers fill one seam:

* **Deterministic** (default, free) — a structured extract per kind (a `tool_result` keeps tool, args,
  salient lines/numbers/paths; a `temp_file` keeps path + outline; a `decision` is already terse). Runs
  in the hot loop and CI; no model call.
* **Cheap-model split/summarize/merge** (pluggable) — Claude Code's pattern (`minhlucvan/claude-code-wiki`
  docs/04): when the working set crosses the budget, batch-summarize the older block with a cheap model
  into ONE dense boundary that **preserves decisions/paths/identifiers/TODOs and drops chatter**, within
  a bounded output budget. Boundaries **recurse** at deeper layers → nested digests, a flat ceiling
  regardless of length.

The digest names its handle, so densification never loses the detail — only relocates it.

## Read-back (the universal read surface)

One tool family reads any kind back, backed by the body store (and `DocWorkspace` for large bodies):

```txt
recall(query)                 search the digest index across kinds/scopes
read(handle)                  the full body
grep(handle, pattern)         matching lines (large bodies) — not the whole body
read_section(handle, section) one bounded slice (large bodies)
```

These are **essentials** — adaptive tool selection never drops them, so a digest is always
re-expandable. This is the no-silent-drop guarantee: information is *relocated*, never deleted.

## Discoverability — how the model knows what it stored

A digest is only useful if the model knows it exists. The model never recalls blindly; it knows what
is in memory through three always-present surfaces, so an entry is **never silently invisible**:

1. **In-context digests.** When a result is demoted/compacted, its digest *stays in the message tail
   and names its handle* — the model still sees `[spent] retrieve(deploy) → DEPLOY WINDOW Thursday …
   · read('mem://tool_result/turn/…')`. It knows the result happened and holds the handle to expand it.
   This is the no-silent-drop guarantee.
2. **The compaction boundary** announces what was folded: `(+N earlier results offloaded — recall to
   retrieve)`, so even fully-compacted older results are known to exist (and findable by search).
3. **The always-on memory index** — `store.render_index()` injects a compact `## Memory` menu each
   turn: one line per entry (`handle — digest`), grouped by kind, newest-first, capped per kind and by
   a token budget. This is the model's *map* of memory — the Tier-2 surface over **all** of it. Entries
   that don't fit the cap are announced as a count, retrievable by `recall(query=…)`.

```txt
## Memory — recall(handle) to expand a digest, recall(query=…) to search
- [decision] mem://decision/conversation/ship-flag — ship behind the new_router flag
- [fact]     mem://fact/user/ui_pref — user prefers dark mode
- [tool_result] mem://tool_result/turn/retrieve-0042 — DEPLOY WINDOW Thursday 14:00 @lan …
- (+37 more — recall(query=…) to find them)
```

So the read path is: **see the index (the menu) → recall(handle) to expand, or recall(query) to search**.
The index is small and cache-stable (append-mostly, capped); the bodies behind it are unbounded. The
model always reasons over a *map it can see*, and pulls the *territory* in only where a step needs it.

## Backends: flash and long-term (pluggable)

The two tiers map to two backends; one model spans RAM to cross-session:

```txt
FLASH      turn                    in-process RAM   working scratch — funneled, dropped at turn end
LONG-TERM  conversation · channel  session store    durable within a conversation
           user · bot              durable store    cross-session: profiles, learned facts, rules
(any tier) large body              DocWorkspace      offload → outline → grep → read_section
```

Promotion is `long_term.remember(...)` of a flash entry that proved durable (a resolved fact, a
concluded decision), consolidated against existing long-term entries (dedup/merge by key).

## Scoped durable memory (the long-term scopes)

The long-term tier is itself **scoped by who shares the fact** — the durability ladder, broad →
specific:

```txt
  bot           everyone, everywhere        office holiday schedule, org-wide rules
  user          one person, any channel     "write my reviews in Vietnamese"
  channel       everyone in one room        "deploy freeze until Monday"
  conversation  one thread only             a one-off passcode
```

One model-facing **`memory` tool** spans them — `save` (upsert in place, never a `_v2` duplicate) ·
`forget` · `recall` (read a key, or list when a fact is over-budget). The agent picks a *scope* (a
durability); it never resolves *whose* — that's the isolation guarantee:

* **Scope refs are resolved host-side, never by the model.** The harness fills the ref from the turn's
  injected identity (`user → user_id`, `channel → channel_id`, …). A turn can only read/write its own
  identity's facts, so user A never sees user B's, and channel X's never leak into Y. A model that
  invents an id is ignored. (This is the same server-side-ACL principle as retrieval.)
* **Most-specific wins.** The always-on `## Memory` index renders `bot → user → channel → conversation`
  (broad → specific) so the most specific fact sits nearest the question and wins on conflict.

The **`## Memory` index** is the always-on Tier-2 surface over durable memory: one capped, newest-first
line per entry, with values inlined under a token budget so most recalls need **zero tool calls** — the
fact is already in the prompt; past the budget an entry degrades to a hint with a "recall to read" note.
This is the [Discoverability](#discoverability--how-the-model-knows-what-it-stored) menu, applied to the
durable scopes.

## Efficiency invariants

```txt
1. Bodies never sit in the prompt; only digests of SELECTED entries do.
2. The prompt is O(budget), not O(history) — selection is CDS-budgeted.
3. Spent / large entries densify (digest) + offload (body) on a token threshold.
4. Everything is re-fetchable by handle — no silent drop.
5. Value beats recency — high-CDS + error entries stay full regardless of age.
6. The digest index is cache-stable (append-mostly).
```

## Implementation status

This generalizes machinery that already exists; it is built **incrementally on a shared substrate**,
not as a big-bang rewrite. All efficiency behavior is opt-in behind the working-set budget — the
default path is byte-identical.

**Live today (the substrate):** CDS + 3-tier router (`network/context_builder.py`), per-hop tiering +
compaction + value pin (`react/funnel.py`), `DocWorkspace` slicing, durable `Memory` + scopes,
`Scratchpad`, `ConversationProfile`.

**Built first (the `tool_result` kind):** the result store + dense digest + `read_result`/`grep`/
`read_section` read-back + engine wiring — see [Tool Use at Scale](./05-tool-use-at-scale.md). This is the
universal substrate landed for one kind, with the `MemoryEntry` shape and the `remember`/`recall`
interface generalizing to the rest.

**Then (the other kinds):** `decision`/`note` (the agent writes its conclusions as small high-utility
entries), `temp_file` (DocWorkspace-backed file entries), folding `Scratchpad`/`Memory`/`ContextNode`/
`ConversationProfile` onto the one `MemoryEntry` + interface.

**Deferred:** auto-offload of a single oversized entry at ingestion; cross-turn persistence of `turn`
-scope entries that prove valuable; an LLM-judged consolidation pass (merge near-duplicate entries).

## Benchmarking

The `tool-use-bench` suite gates the `tool_result` slice (tail boundedness, digest fidelity, refetch,
value pin, catalog leanness — see [Tool Use at Scale](./05-tool-use-at-scale.md)). As kinds are added, the
same harness gates them with the same metrics, because the storage/valuation/read-back is identical
across kinds:

```txt
Selection precision   useful entries injected / entries injected
Digest fidelity       decisions/needles surviving the digest        → ≥ floor
Tail boundedness      prompt growth per hop                         → near-flat under compaction
Refetch success       read(handle) returns the exact body           → 1.0
Cross-kind coverage   each kind round-trips remember → recall(full)  → 1.0
```

## Design principle

A capable agent is not the one that crams everything into the prompt, nor the one that forgets. It is
the one that keeps a **dense, valued index of everything** — context, decisions, notes, results, files —
and pulls the detail back exactly when a step needs it.

## Related

* [ReAct Context Management](./04-react-context-management.md) — the CDS / tier / funnel / compaction
  machinery this generalizes across kinds.
* [Tool Use at Scale](./05-tool-use-at-scale.md) — the `tool_result` application (built first).
* [Shared Context](./07-shared-context.md) — one handle every component uses to reach these scopes.
* [Task Execution Mode](./13-task-execution-mode.md) — long-rail work that produces many entries.
* [Stateless Serving](./16-stateless-serving.md) — `to_json` / `restore` ride the long-term tier in a
  session snapshot, so working memory survives a restart / moves between replicas.
