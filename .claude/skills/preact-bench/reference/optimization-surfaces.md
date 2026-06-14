# Optimization surfaces & invariants — the shared knowledge base

This is the canonical reference the `bench-first-dev`, `optimize-verdict`, and `production-ready`
skills link to. It answers one question: **when a verdict says something is wrong, where do you
change the SDK to fix it — without breaking what makes the SDK the SDK?**

The governing rule (from `CONTRIBUTING.md`, `docs/preact.md`): **new capability or tuning is a data
row, never an interpreter branch.** If your instinct is to add an `if` in the engine, stop — the
right surface is below.

## The surfaces, smallest-blast-radius first

Prefer the lowest-risk surface that fixes the failing **gating** check. Escalate only if it can't.

### 1. The flat weight dict — `agent_sdk/lobes/weights.py` (`DEFAULT_LOBE_WEIGHTS`)
One sparse dict of floats; per-bot overrides arrive via `policy.flow_lobe_weights` →
`merge_lobe_weights`. Keys:
- `prior_<lobe_id>` — baseline activation of a lobe (raise to fire it more, lower to fire it less).
- `min_<lobe_id>` — activation threshold to run the lobe.
- `budget_<lobe_id>` / `budget_memory|skill|cognition` — token budgets (trim context pressure).
- `edge_<src>__<dst>` — downstream activation flow from an upstream lobe that fired **and completed**.
- `w_<signal>` — global per-signal weights (`w_anaphora`, `w_short_query`, `w_scope_gate`,
  `w_route_complex`, `w_simple_shape`, `w_skills_declared`, …) — the context features that drive routing.
- `path_<path>__<member>` — per-path member bias.

Activation formula (`agent_sdk/network/activation.py`):
`a_j = prior_j + Σ_k w_k·signal_k(ctx) + Σ_i edge_{i→j}·a_i + Σ_p path_bias_{p→j}·score_p`.
This is the **first** surface to try for routing/activation problems (a skill or lobe firing too
much / too little, a path chosen with low confidence).

### 2. Registry rows — `agent_sdk/lobes/registry.py` (`LobeRegistry.add_row`)
Add or override a lobe by id with a data row (`{id, signals, edges, prior, min_activation, …}`).
Every mutation re-validates the forward DAG (no cycles) and pinned-edge protection. Use when a new
context discipline or edge is needed — still data, not a branch.

### 3. Plugins — `agent_sdk/plugins/` (one folder per plugin)
A plugin contributes a whole capacity surface (lobes/stages/flows/skills/tools, even MCP servers).
`SafetyPlugin` (cite/filter) and `FormatPlugin` (styling) are default-on but toggleable; the rest are
opt-in. Use when the capability is an optional add-on rather than intrinsic to every agent. New
optional capability ⇒ a plugin, not core. See `docs/concepts/plugins.md`.

### 4. Prompt / lobe content & skill content
- Lobe `system_prompt` / directives (e.g. the on-demand skill directive `_ON_DEMAND_DIRECTIVE`) —
  for behavior shaping (single-skill-per-turn nudge, "read one section then answer").
- Skill SOPs under `benchmarks/skillbench/dataset/skills/*/SKILL.md` (and any real skill packs) —
  for follow/funnel content gaps (a skill that dumps too much, or lacks a mandate the bench checks).

### 5. Engine answer-capture / runtime seams (rare, highest blast radius)
Some defects are genuine runtime bugs, not tuning (e.g. ANALYSIS.md's `_text_of` mis-reading a
thinking block as the answer; forced-final hop emitting no prose). These touch `agent_sdk/engine.py`
or a client. They are legitimate fixes but require the **full** invariant suite + a regression test
proving the behavior, and the smallest change that restores the contract. Never reach here first.

## How to diagnose which surface — the probe/inspection layer

Don't guess; read the trace. Per failing check:
- Open the bench's `--report` HTML (the viewer: timeline, flow, lobes, context provenance, tools, hints).
- `agent_sdk/probe.py:probe(agent, query)` → a `ProbeRecord` (path+score, per-lobe activation rows,
  per-stage ReAct steps, llm/tool calls, usage, `hints`, `attention` funnel, `skill_selection`).
  It never raises — partial traces still render.
- `agent_sdk/inspection.py`: `inspect_lobe_axis` (which lobes fired and why — state nodes),
  `inspect_flow_axis` (selected steps, loop modes, tools, disabled), `snapshot_engine`,
  `suggest_axis_optimizations(snapshot)` → **pure weight-patch proposals** (e.g. "step produced no
  lobe nodes → disable it"; "lobe state nodes all inactive → lower its prior"). These proposals are
  exactly the candidate fixes for surface #1.
- `agent_sdk/bench.py:Harness/Scenario/Report` — programmatic routing checks (path/lobes/flow/status)
  with no LLM, for fast red→green on activation without paying for the model.

## The five invariants — never trade these away

Test-enforced; a change that breaks one is a regression, not a trade-off. After **every** change run:
```bash
uv run python -m pytest -q                       # full suite (includes the three below)
uv run python -m pytest tests/test_sdk_isolation.py tests/test_lobe_network.py tests/test_pinned_lobes_parity.py -q
uv run ruff check agent_sdk && uv run ruff format agent_sdk
```
1. **Leaf isolation** (`tests/test_sdk_isolation.py`) — `agent_sdk` imports only stdlib + its deps +
   itself; never a host package.
2. **Default-network parity** (`tests/test_lobe_network.py`) — no-plugin agent ≡ the canonical default
   network in `(layer, order)`. Tuning a *default* weight that shifts parity is a regression; per-bot
   overrides are the right place for bot-specific bias.
3. **Citations-mandatory** (`tests/test_pinned_lobes_parity.py`, `PINNED_LOBES`) — `cite`/`filter`
   (pinned) + `synthesize` survive any removal; ground-or-refuse can't be stripped.
4. **Core determinism** — recognition, activation, attention/budget, flow resolution are pure
   functions of `(spec, context)`; never an LLM judging the pipeline.
5. **Benches live-only** — no LLM stubs in `benchmarks/`.

## Hard "never"s for any optimization
- Never weaken, skip, or loosen a **gating** check to turn a verdict green.
- Never branch the interpreter on agent identity; never stub a bench.
- Never break an invariant to land a fix — if the only fix that works breaks one, stop and report.
- Diagnostics are not gates — don't optimize against flappy diagnostics.
