# benchmarks — the agent-sdk evaluation + improvement suite

Each benchmark is one evaluable slice of the SDK: it drives the **real** engine on the public surface,
emits a deterministic **verdict** (`READY` / `NOT_READY` / `UNMEASURED`), and plugs into a
self-improving **ratchet**. Together they let an agent loop *bench → analyze → improve → repeat* over
every core concept. Everything here is **leaf-pure** — it imports only `agent_sdk.*` + `benchmarks._shared`,
never the host project (enforced by `tests/test_sdk_isolation.py`).

## Two tiers

- **Free / deterministic** — no provider, no network. The SDK's recognition, activation, flow
  resolution, and metacognition are pure functions of `(spec, context)`, read via the no-LLM
  `PreactAgent.inspect` (or called directly). Fast, reproducible — ideal for the loop and CI.
- **Live** — a real `PreactAgent` against a real provider (`--live`). **No stubs** (a stubbed bench is
  an integration test → it belongs in `tests/`). Live tiers may pool variance with `--trials N`.

## The suite

| Bench | Concept / axis | Tier | Certifies |
|---|---|---|---|
| [`attentionbench`](attentionbench/) | context (OY) | free | node selection (relevant outranks flooders) + lobe activation (recall always-on, grounding on grounded paths) |
| [`flowbench`](flowbench/) | flows (OX) | free | each intent → right flow + step order, the cite→filter grounding contract, determinism, graceful fallback |
| [`corgictionbech`](corgictionbech/) | metacognition | free | monitor→regulate decision table, apply/observe channel, the pinned-step guard (`cite`/`filter` never skippable) |
| [`fanoutbench`](fanoutbench/) | subagent fan-out | free + live | per-worker context isolation (zero leakage), bounded failure, ordering determinism, registry resolution; live decompose→fan-out→synthesize fidelity |
| [`toolbench`](toolbench/) | tool use | free + live | `@tool` specs, `FunctionToolRuntime`/`CompositeToolRuntime`/embedded MCP, `ToolSelectLobe` adaptive exposure, the live agentic `tool_loop` |
| [`skillbench`](skillbench/) | skills | live | skill activation precision/recall, follow (answer obeys mandates), funnel (navigate not dump) |
| [`taskbench`](taskbench/) | tasks | live | long-rail task planning + execution |
| [`extensionbench`](extensionbench/) | plugins / MCP | live | a plugin is a plug-and-play surface (lobes/stages/flows/tools + its own MCP server) |
| [`agentbench`](agentbench/) | memory / recall | live | an integrated mission: ingestion amid chatter, supersession, needle recall, bounded context |
| [`coding-agent-bench`](coding-agent-bench/) | full agent | live (+ `--replay`) | the reference agent end-to-end (survey→plan→investigate→document) on a real codebase |

## The verdict model

Every bench composes its verdict via `_shared/verdict.py:compose_verdict` →
`{status, reasons, gates, metrics}`, prints a `… : X/Y checks pass · verdict <STATUS>` line, and
**exits `0` iff READY**.

- **READY** — every required mode measured and all **gating** checks pass.
- **NOT_READY** — a gating check failed (the thing to fix).
- **UNMEASURED** — a required mode produced no evidence (e.g. a live tier with no creds). Never a pass.

**Gating checks decide the verdict; diagnostics never do** — flappy signals (token ratios, etc.) are
recorded but don't gate. Deterministic gates are truth; there is no LLM judge that can rescue a red gate.

## Running

```bash
# one bench
python benchmarks/flowbench/run.py                 # free — no creds needed
python benchmarks/skillbench/run.py --live --report # live — writes results/skillbench.html

# the free unit gate (CI floor)
bash benchmarks/ci-free-gates.sh

# the full readiness sweep → matrix + trend (the free benches show READY even with no creds)
bash benchmarks/loop/ladder.sh
LOOP_MODEL=MiniMax-M2.7 LOOP_TRIALS=3 bash benchmarks/loop/ladder.sh
```

Live tiers auto-load `.env` (SDK-local first, then repo root) via `_shared/provider.py`
(`ANTHROPIC_*` or `MINIMAX_*`).

## The improvement loop (the ratchet)

Verdicts feed a moving-baseline ratchet so the SDK — and the benches — get *better and better*:

- `loop/` — `ladder.sh` sweeps the suite; `snapshot.py` writes the readiness matrix + an append-only
  trend (`history.jsonl`).
- `_shared/improve.py` + `improve_cli.py` — the deterministic ratchet: `improve/best.json` (the moving
  baseline each wave must beat), append-only `wave-NNN/` records, `journal.md`, `history.jsonl`, and a
  frozen `releases/` + `SOTA.json` champion layer. The model diagnoses/implements; **the CLI decides
  keep-or-revert** (status rank + passing-gate count) — never model-judged.

Drive it with the Claude Code skills in `../.claude/skills/` — `preact-bench` (run/parse),
`optimize-verdict` (per-verdict fix loop), `bench-harden` (raise the bar), `improve-loop`
(continuous), `production-ready` (drive all to READY) — or autonomously via the
`../.claude/workflows/improve-loop.js` Workflow.

## Layout

| Path | What |
|---|---|
| `<bench>/run.py` | the runner (modes → `compose_verdict`, exit 0 iff READY) |
| `<bench>/METHOD.md` | the optimization approach: the lever each failing gate maps to + metrics/gates |
| `<bench>/dataset/` | scenarios (where dataset-driven) |
| `<bench>/results/` | run outputs `<label>.{json,html}` (gitignored) |
| `<bench>/verdicts/`, `<bench>/improve/` | committed verdict snapshots + the ratchet state |
| `_shared/` | `verdict.py`, `provider.py`, `report.py`, `embed.py`, `improve.py`/`improve_cli.py`, **`TEMPLATE.md`** (the standard) |
| `_template/` | copyable skeleton for a new bench |
| `loop/` | the sweep ladder + trend |
| `MIGRATION.md` | porting the monorepo benches onto the SDK surface (status + recipe) |

## Adding a benchmark

Read `_shared/TEMPLATE.md` (the standard a conforming bench satisfies: a **method**, **metrics +
gates**, and a place in the loop), then `cp -r _template <name>` — or use the **`bench-scaffold`**
skill. `toolbench` is the worked reference for a tool-driven bench; `flowbench` / `attentionbench` /
`corgictionbech` for deterministic engine benches.
