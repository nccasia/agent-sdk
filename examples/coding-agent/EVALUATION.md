# Evaluating `agent_sdk` by building a real coding agent

This is a candid evaluation of the SDK from the inside — written while building
the coding agent in this folder and running it **live against MiniMax-M2.7** (the
project's Anthropic-compatible endpoint) on a real filesystem sandbox. The agent
genuinely edits files on disk and runs the real test suite.

## What was built

A multi-stage coding agent on the SDK's public surface only:

- **6 production-shaped lobes** (`triage`, `explore`, `plan`, `implement`, `verify`, `summarize`) — `coding_agent/lobes.py`
- **6 stages** + **3 flows** (`question` / `quick_fix` / `feature`) — `coding_agent/agent.py`
- **Real fs/exec tools** (`list_dir`, `grep`, `run_command` via `@tool`) + the SDK's `PluginWorkspace(driver="local")` for `fs.*` — `coding_agent/tools.py`
- A deterministic offline demo + a pytest suite that verifies real edits and a passing `pytest` (no network), and a `live_run.py` for the real LLM.

Total agent code: ~300 lines. **That number is the headline positive** — a
working multi-stage, tool-using, filesystem coding agent in ~300 lines of
declarative composition, no framework plumbing.

## Live run results (MiniMax-M2.7, task: "add a multiply function + test")

| Mode | Wall | Input tok | Output tok | Outcome |
|---|---|---|---|---|
| isolated stages (default) | 48.5s | 6,679 | 2,132 | ✅ multiply added, `pytest` 3 passed |
| `share_history=True` | 40.6s | 13,320 | 1,713 | ✅ passed, but model asked clarifying questions mid-pipeline |

Both completed correctly and the **independent** verifier (the agent's claims are
not trusted — `live_run.py` re-runs pytest itself) confirmed it. The SDK's
"degrade, never lose the turn" behavior held even when the model wandered.

## What's good (API + behavior)

1. **Composition is genuinely declarative.** Lobes/stages/flows are data; a new
   capability is a row, never a branch. Authoring a lobe (metadata + one
   `activation` + a `system_prompt`) and a stage (`stage(id, lobes=…, loop=…,
   tools=…)`) reads cleanly. The `Layer` enum + `@tool` introspection are friction-free.
2. **`inspect()` (no-LLM probe) is excellent.** Being able to assert routing
   (`agent.inspect(task).path`, `.flow`, activated lobes) deterministically, in
   unit tests, with zero token cost, is the single best ergonomic. The example's
   routing tests are fast and free.
3. **`FakeClient` makes the whole agent CI-testable.** The pipeline runs
   end-to-end on real files with a scripted model — no network, fully
   deterministic. This is a real strength for agent development.
4. **The event stream is clean.** `async for ev in agent.act(...)` with
   pattern-matchable `ToolCall`/`ToolResult`/`StageStart`/`TextDelta` made the CLI
   trivial and is the same shape you'd publish over SSE.
5. **`PluginWorkspace` + `@tool` interop is seamless.** Mixing SDK-provided `fs.*`
   tools with custom `run_command`/`grep` tools "just worked" via the composite runtime.

## Friction & findings (with fixes)

### 1. Multi-stage note carry-forward was hardcoded — **fixed**
The engine only carried `plan`/`research` stage output forward as notes; a
custom pipeline (`explore → plan → implement → verify → summarize`) lost every
stage's context. Fixed in the engine to carry **every non-final stage's** output
forward as a labeled note. (Verified: `verify`'s prompt now contains `[plan]` and
`[explore]` notes.)

### 2. Stages are isolated — re-exploration is the dominant cost — **partial fix, real trade-off**
Each agentic stage starts from the same base messages, so `verify`/`summarize`
**re-read files the `explore` stage already read**, and in one run the model
declared multiply "was already added" because it re-discovered prior work. I
added an opt-in `share_history=True` (threads message+tool history across
stages). But the live A/B showed it's **not a clean win**: it *raised* input
tokens (6.7k→13.3k) and the conversational framing made the model treat the
pipeline as a clarifying dialogue ("I don't have a new change to implement yet").

**Recommendation (not yet built):** the right fix is a structured **evidence
channel** — carry forward the *tool results / files read* as compact, labeled
context (the SDK's `Blackboard` + compression-invariant design already anticipate
this) rather than replaying the staged conversation as dialogue. This would cut
re-reads without the token blow-up or the dialogue confusion.

### 3. Lexical routing is brittle for natural phrasing
"how does **add** work" routes to `feature` because `add` is a feature keyword.
`is_question` gating helps but breaks on "can you **add** X?". **Recommendation:**
make semantic activation (embed `use_when` vs the query) a first-class, one-line
opt-in (`PreactAgent(..., embed=…)` already exists as a seam but isn't wired into
flow recognition). Routing should blend lexical + semantic when an `embed` is present.

### 4. `single`-loop stages can't satisfy a model that wants tools
The `summarize` stage (`loop="single"`, no tools) prompted MiniMax to emit
**pseudo-tool markup as plain text** ("Tool Used: read_file …"). **Recommendation:**
either (a) document that a stage which might need tools should be `agentic`, or
(b) detect tool-shaped text in single stages and surface a warning in the trace.

### 5. No token-level streaming from real clients
`TextDelta` fires once per stage with the full text; `AnthropicClient` doesn't
stream. The docs advertise `stream.text_stream` token-by-token. **Recommendation:**
implement true streaming in `AnthropicClient`/`OpenAIClient` (the engine already
has the `TextDelta` plumbing).

### 6. `@tool(requires=[...])` is captured but not enforced
`run_command` is arbitrary code execution with no gate. The `requires` metadata
exists but the engine ignores it. **Recommendation:** add a per-tool authorization
seam (a `tool_guard(name, input, requires) -> bool|raise`) and a `PluginGuardrails`
hook for tool calls, not just turn pre/post. Important for any agent with `run_command`.

### 7. Minor ergonomics
- `Engine` reads `stage.description` for the share-history transition; stages
  without a description produce a bare id — fine, but a `purpose` field distinct
  from a human description would be cleaner.
- The default network ships `cite`/`filter`; a coding agent doesn't want them.
  It was easy to replace the whole network, but a `network="bare"` preset (no
  output-contract lobes) would save a step for non-RAG agents.

## Performance read

- The deterministic core (recognize + activate + resolve) is **sub-millisecond**;
  all cost is the model + tools, as designed.
- For a trivial change, the 5-stage `feature` pipeline spends ~8–13k tokens and
  ~45s — most of it **re-exploration** (finding #2). A coding agent should route
  small changes to a shorter flow; the `quick_fix` flow (4 stages) helps, and an
  even shorter `tiny_edit` flow (implement→verify) would help more. The framework
  makes that a one-line flow addition.

## Bottom line

The SDK held up well: a real, working, filesystem-mutating coding agent in ~300
lines, fully testable offline, with free routing introspection. The architecture
(deterministic core + pluggable seams + declarative network) is sound and
pleasant. The highest-value next investments are: **(a) a structured cross-stage
evidence channel** (the biggest behavior + cost win), **(b) semantic flow
recognition**, and **(c) a tool-authorization seam** for `run_command`-class tools.
