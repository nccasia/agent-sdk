# SDK contracts reference

`sdk/contracts/` is the **dependency-free base** of the SDK — the protocols and
data models everything else builds on. Nothing here imports the activation
network or any concrete behavior, so these types stay stable and cheap to import.

## `llm.py` — `LlmCall`

The one injectable LLM-call seam. A `Protocol`, so any matching async callable
works (production wrapper, or a `FakeLlm` in tests).

```python
async def __call__(*, stage: str, system: str | list, messages: list[dict],
                   max_tokens: int, temperature: float | None = None,
                   tools: list[dict] | None = None, count_usage: bool = True) -> Awaitable[Any]
```

- `stage` selects which model config / accounting applies (the impl resolves it).
- `system` may be a plain string or a cache-split block array.
- Returns a provider message (`.content`, `.stop_reason`).

## `services.py` — `LobeServices`

The bundle of injected side-effect seams a lobe behavior may use. All optional;
a lobe declares only what it needs.

| field | purpose |
|---|---|
| `llm` | the `LlmCall` above |
| `execute_tools` | run a message's `tool_use` blocks (for `tool_loop`) |
| `embed` | embedding function (e.g. citation verification, semantic recall) |
| `post_internal_context` | push internal context to a backend |
| `session_factory` / `redis` | durable-store handles |
| `emit` | progress/telemetry callback |

## `turn.py` — per-turn data

- **`TurnContext`** — the narrow per-turn state passed to lobes: `query`,
  `policy`, `services`, `active_path`, `blackboard`, `scratchpad` (turn RAM),
  `lobe_outputs`, `memory_items`, `task_items`, … Intentionally *not* the
  interpreter, so lobes are testable in isolation.
- **`PromptContribution`** — one lobe-owned prompt block with a `stability`
  (`stable` / `slow` / `turn`) for cache-aware composition.
- **`LobeResult`** — envelope for class-based lobe execution (`value`, `nodes`,
  `prompt`, `metadata`).
- **`StageResult`** — a single flow step's result (`text`, `context_nodes`,
  `tool_calls`, token/latency counters, `metadata`).

## `tools.py` — the tool boundary

- **`ToolRuntime`** (`Protocol`) — `get_tool_specs() -> list[dict]` +
  `async call_tool(name, inp, retrieved_chunks, already_read) -> str`. The
  generic contract; concrete KB/MCP/skill/memory runtimes (in the project)
  implement it.
- **`CompositeToolRuntime`** — fans `get_tool_specs`/`call_tool` across a list of
  runtimes, owning the name→runtime routing. `external_names()` marks tools from
  external (HTTP) MCP installs so adaptive selection never scores them out.

## `memo.py` — the answer contract

The structured final-answer types — the only shapes allowed to cross step
boundaries (the compression invariant; raw KB chunks never do):

- **`Citation`** — `(chunk/source ref, span)` grounding a claim.
- **`Claim`** — text + supporting citations + confidence.
- **`Memo`** — an aspect's claims + unresolved questions.
- **`FinalEnvelope`** — the structured final message the harness emits.

## `pins.py` — `PINNED_LOBES`

`frozenset({"cite", "filter"})` — the engine's one structural lobe-name
commitment: the output-contract lobes that bypass the activation threshold and
can never be deactivated (the PRD ground-or-refuse invariant). The activation
network and the metacognition regulator both read this single source of truth.
`rag_core.policy.schema.PINNED_LOBES` is the policy-validation copy, kept
identical by [`tests/test_pinned_lobes_parity.py`](../../../tests/test_pinned_lobes_parity.py).
