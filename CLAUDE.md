# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`agent-sdk` (codename **PreAct**) is a standalone, publishable Python SDK for building AI agents
whose reasoning pipeline you fully control — *pre-structured acting*, not free-acting ReAct. It is a
**git submodule** (`.git` is a gitfile → `../../.git/modules/packages/agent-sdk`) with its own
history, packaging, and CI; it is **not** governed by the monorepo's root `CLAUDE.md`, and it is a
different package from `packages/agent-core` (which is the in-tree engine that this SDK was extracted
from). Treat this directory as its own repo.

Authoritative docs: `README.md`, `CONTRIBUTING.md`, `docs/api.md` (public surface), `docs/preact.md`
(the model). Note: some files under `docs/concepts/` are ported from agent-core and still use the
old `agent_core` / `BotPolicyInterpreter` names — the model maps over, but trust `README.md` +
`CONTRIBUTING.md` for current package names.

## Commands

```bash
uv sync                                  # install (or: pip install -e ".[dev]")
uv run python -m pytest -q               # full suite (270+ tests; testpaths = tests/ + agent_sdk/)
uv run python -m pytest tests/test_reply_flow.py -q          # one file
uv run python -m pytest tests/test_reply_flow.py::test_name  # one test
uv run ruff check agent_sdk              # lint (E/F/I/B/UP/ASYNC/SIM, line-length 100; E501 off)
uv run ruff format agent_sdk             # format (double quotes)
```

`pyproject.toml` sets `testpaths = ["tests", "agent_sdk"]` and `asyncio_mode = "auto"`: the root
`tests/` integration suite **plus** plugins' co-located unit tests (e.g. `agent_sdk/plugins/tasks/tests/`)
run by default. No `@pytest.mark.asyncio` needed.

Optional extras: `openai`, `redis`. Dev extra pulls `pytest`, `pytest-asyncio`, `fakeredis`.

### Benchmarks are live-only

`benchmarks/` (agentbench, taskbench, extensionbench, skillbench, coding-agent-bench) take **no LLM
stubs** — a stubbed bench is an integration test and belongs in `tests/`. They require provider
credentials and a `--live` flag, e.g. `python benchmarks/extensionbench/run.py --live` (emits
`READY`/`NOT_READY`). The free deterministic gate is `bash benchmarks/ci-free-gates.sh`.

## The model (read before touching the pipeline)

A turn is a deliberate pipeline split across two independent, tunable axes, with **metacognition**
supervising both (per `README.md` / `docs/preact.md`):

- **OY — context axis = `lobes`.** Small passive thinking units; each fires the right context +
  local prompt behavior for one slice of the turn. Organized by **domain package**, each owning a
  `lobes` subpackage that exports `LOBES`:
  `agent_sdk/{memory,skills,tools,cognition,expression}/lobes/` (+ `paths/` owns `PATHS`).
  `agent_sdk/lobes/network.py` just concatenates the domains in B-layer order; the engine re-sorts
  by `(layer, order)`.
- **OX — time axis = `stages` / `flows`.** A `Flow` is an ordered pipeline; each `Stage` owns its
  lobe slice, loop mode (`none` / `single` / `agentic`), and tool allowlist.
- **`intent` / `paths` — the router.** Each turn an intent biases the lobes and selects the flow.
  Recognized by deterministic free signals **or** an LLM classifier — your choice.
- **`metacognition` — always on.** `monitor → regulate`: adjust the lobe slice, retry, or skip a
  step, but **never** a pinned safety step (`cite` / `filter`).

**New capability = a registry row / plugin, never an interpreter branch.** Add a default lobe by
adding one entry to its domain's `LOBES` (or per-agent via `LobeRegistry.add_row`); add a stage/flow
via the registries — do not branch the kernel on agent identity.

`agent_sdk/engine.py` is the generic kernel (~72KB, the largest file); `agent_sdk/agent.py` is the
`PreactAgent` façade over it. The default network is the faithfully-ported agent-core production
network (`agent_sdk/preact/production.py`): 18 lobes, 8 paths, named flows. `Lobes`/`Stages`/`Flows`
in `agent_sdk/preact/defaults.py` expose `.default()` (production) and `.minimal()` (lightweight).

## Core vs. extensions (plugins)

A hard, intentional line:

- **Core** = domain-free lobes intrinsic to *every* agent, in the domain packages above
  (`memory`, `skills`, `tools`, `cognition`, `expression`). **Not toggleable.**
- **Extensions** = `agent_sdk/plugins/`, one folder per plugin (`safety`, `format`, `tasks`, `mcp`,
  `otel`, `guardrails`, `workspace`, `support_triage`). A plugin contributes a full capacity surface
  (lobes/stages/flows/skills/tools, even its own MCP servers). `SafetyPlugin` (cite/filter grounding)
  and `FormatPlugin` (styling) are **default-on but toggleable**; the rest are opt-in. Manage via
  `PluginRegistry` / `builtin_registry()` (in `agent_sdk/plugins/__init__.py`).

When adding a capability, ask: intrinsic to every agent (core) or optional add-on (extension)? When
in doubt, prefer an extension. An agent with no extra plugins is **byte-identical** to the default
network.

## Invariants — a violation is a regression, not a trade-off

Enforced by the test suite (`CONTRIBUTING.md`):

1. **Leaf isolation.** `agent_sdk` imports only stdlib + its third-party deps (`anthropic`, `numpy`,
   `pydantic`, `cachetools`, optionally `openai`/`redis`) + other `agent_sdk` modules — **never a
   host application package**. Gated by `tests/test_sdk_isolation.py`. This keeps the SDK standalone
   and publishable; do not reach into the surrounding monorepo.
2. **Default-network parity.** No-plugin agent ≡ default network: `default_lobe_objects()` returns
   the same canonical lobes in the same `(layer, order)`. The default registry is the *degenerate
   network* that reproduces the legacy decision table at default weights. Parity matrix:
   `tests/test_lobe_network.py` — breaking it is a regression, never a tuning outcome.
3. **Citations-mandatory.** `cite` / `filter` (pinned) + `synthesize` survive any removal — no
   plugin, weight, or removal strips the ground-or-refuse guarantee. `SafetyPlugin` can be
   *disabled* by an integrator but never *stripped* by another plugin. `PINNED_LOBES` is canonical.
4. **Determinism in the core.** Intent recognition, activation, attention/budget, and flow
   resolution are pure functions of `(spec, context)` — never an LLM judging the pipeline.
5. **Benches are live-only** (see above).

## Where to look first

- Public API surface → `agent_sdk/__init__.py` (the `__all__` list) + `docs/api.md`.
- The façade → `agent_sdk/agent.py` (`PreactAgent.query` one-shot, `.act` streaming events).
- The kernel → `agent_sdk/engine.py`; per-turn contracts (`LlmCall`, `LobeServices`, `TurnContext`,
  memo models, `ToolRuntime`, `PINNED_LOBES`) → `agent_sdk/contracts/` (and re-exported flat from
  `agent_sdk/_blocks.py`).
- Activation / context funnel → `agent_sdk/network/activation.py` + `network/context_builder.py`;
  the ReAct funnel → `agent_sdk/react/funnel.py`.
- Lobes → `agent_sdk/lobes/` (base `runtime.py`, `registry.py`, `weights.py`, `network.py`); concrete
  lobes live in the domain packages' `lobes/` subdirs.
- Flows / stages → `agent_sdk/flows/` (`flow.py`, `registry.py`, `defaults.py`, `stages/`) and
  top-level `agent_sdk/stages.py`.
- Paths / intent → `agent_sdk/paths/` (qna, research, clarify, relational, onboarding) +
  `agent_sdk/selection.py`, `signals.py`.
- Metacognition → `agent_sdk/metacognition/` (`controller.py`, `monitor.py`, `regulator.py`).
- Memory → `agent_sdk/memory/` (durable, universal, scratchpad, semantic_cache, recall_tool,
  prefetch); stores → `agent_sdk/stores/` (in-memory default + redis).
- Skills (progressive disclosure) → `agent_sdk/skills/` (parser, compiler, runtime, packs, prompt).
- LLM clients → `agent_sdk/clients/` (`anthropic_client`, `openai_client`, `minimax_client`,
  `mixed`, and the deterministic `fake.py` `FakeClient` for offline tests).
- Tools / MCP → `agent_sdk/tools/`, `agent_sdk/mcp.py`.
- Inspection / trace → `agent_sdk/inspection.py`, `probe.py`, `report.py` (HTML), `viewer.py`,
  `bench.py`.
- Runnable reference → `examples/coding-agent/` (triage→explore→plan→implement→verify on a real FS,
  ~300 lines on the public surface; `demo.py` is offline-deterministic, `main.py --inspect` probes
  routing).

## Project skills (`.claude/skills/`)

A benchmark-driven workflow suite operates this repo's live-only benches and the SDK's tuning
surfaces. Invoke by name (`/preact-bench`, etc.); they compose, and share one knowledge base under
`.claude/skills/preact-bench/reference/` (benches, the verdict model, the optimization surfaces +
the five invariants):

- **`preact-bench`** — run a bench / the ladder and report a normalized verdict (the run+parse primitive).
- **`bench-first-dev`** — red→green: write the failing bench scenario first, then the minimal fix.
- **`optimize-verdict`** — the autonomous per-verdict loop: diagnose a failing gating check from the
  probe trace → smallest legitimate fix (weight / registry row / plugin / prompt / skill content) →
  re-run → invariants green → commit. Hard-stops rather than weakening a gate.
- **`production-ready`** — orchestrator: full readiness matrix, then drive every NOT_READY/UNMEASURED
  to READY via `optimize-verdict`, keeping all five invariants green (single drive-to-green pass).
- **`bench-harden`** — the "improve the benchmark" half of the ratchet: add discriminating cases
  (near-neighbor / refusal / adversarial / per-turn) so a green bench surfaces the next real gap.
- **`improve-loop`** — the continuous feedback loop: sweep → analyze the trend → `optimize-verdict`
  where failing, `bench-harden` where green, repeat. Drive hands-off with `/loop improve-loop`.
- **`bench-scaffold`** — author a new benchmark from `benchmarks/_template/` to the standard
  (`benchmarks/_shared/TEMPLATE.md`): a method/optimization approach (`METHOD.md`) + metrics/gates +
  a live `run.py` emitting the verdict contract + the `improve/` ratchet. For "create many benchmarks".
- **`workflow-improve`** — run the loop autonomously via Claude's **Workflow (workflow.js)** feature:
  `Workflow({ name: "improve-loop", args: { bench, waves, model } })` drives diagnose → implement →
  re-bench → keep-or-revert per wave, committing kept waves.

### Feedback-loop infrastructure (`benchmarks/`)
- `benchmarks/loop/` — `ladder.sh` sweeps the ladder; `snapshot.py` writes the readiness matrix + the
  append-only trend (`history.jsonl`).
- `benchmarks/_shared/improve.py` + `improve_cli.py` — the **moving-baseline ratchet** (best.json +
  append-only `wave-NNN/` + journal + history + `releases/`/`SOTA.json`), generalized over the
  `compose_verdict` shape. The keep/revert decision is deterministic (status rank + passing-gate
  count), never model-judged.
- `benchmarks/_shared/TEMPLATE.md` + `benchmarks/_template/` — the benchmark standard + scaffold.
- `.claude/workflows/improve-loop.js` — the Workflow-tool driver of the wave loop.

`.claude/settings.json` allowlists the read-only bench/test/loop/ratchet commands these skills run.

## Conventions

- Conventional-commit subjects: `feat(...)`, `fix(...)`, `refactor(...)`, `docs(...)`.
- New behavior needs a test; new public API needs a note in `docs/api.md`.
- Model ids are passed by the integrator (e.g. `AnthropicClient("claude-opus-4-8")`) — the SDK is
  provider-agnostic and pins no model.
