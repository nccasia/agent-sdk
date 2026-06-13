# The five benches — what each measures, dataset, modes, CLI

All live under `benchmarks/`, all are **live-only** (a stubbed bench is an integration test and
belongs in `tests/`), all expose `run.py --live`, all exit `0` only when **READY**. Run from the
`packages/agent-sdk` dir. Free deterministic floor for all of them: `bash benchmarks/ci-free-gates.sh`.

| Bench | Measures | Live CLI | Free tier |
|---|---|---|---|
| **skillbench** | Skill subsystem: parse SOP → structure, map skill → stages/tools, activation P/R, follow (answer obeys mandates), funnel (navigate not dump) | `python benchmarks/skillbench/run.py --live --report [--trials 3] [--model id]` | `lint`/`parse`/`mapping` groups are pure functions, but `run.py` requires `--live` |
| **taskbench** | Long-rail task planning/execution: 11 capabilities (decompose, drive-to-done, state-carry, tool orchestration, dependency order, branching, replan, error recovery, …) | `python benchmarks/taskbench/run.py --live [--capability N] [--task id]` | per-capability `UNMEASURED` where the SDK isn't wired (e.g. parallel fan-out) |
| **agentbench** | One integrated ~1500-turn mission: memory ingestion amid chatter, fact supersession, needle recall, distractor resilience, cross-session memory, bounded context | `python benchmarks/agentbench/run.py --live --report [--model id]` | none (all cases LLM) |
| **extensionbench** | Plugins as first-class plug-and-play surface: plugged → lobe/path/tool active; unplugged → gone; MCP server discovery + in-process transport | `python benchmarks/extensionbench/run.py --live --report [--model id]` | structure is unit-tested in `tests/test_plugins_full_surface.py` |
| **coding-agent-bench** | The reference agent end-to-end (survey→plan→investigate→document) on a real codebase | `python benchmarks/coding-agent-bench/run.py --live [--trials 3] [--target dir] [--update-baseline]` | `--replay` (scripted model, no provider) |

## skillbench groups (the richest verdict surface)

Six groups; the first three are deterministic (free), the last three need the provider:

- **lint** (free) — adversarial fixtures (`_bad_*`) must be **rejected** by some gate.
- **parse** (free) — per skill: description quality, body sections, ToC navigability for large files, checklist validity.
- **mapping** (free) — skill appears in its declared stage, absent off-stage; on-demand has index+ActivateSkill, eager is inlined.
- **activation** (LLM) — model activates the right skill, not distractors. Gates: recall ≥ 0.8, precision ≥ 0.8 (pooled over `--trials`).
- **follow** (LLM) — answer obeys the scenario's `must_include` / `must_not_include` / `must_include_any` mandates.
- **funnel** (LLM) — large bundles get navigated (`skill.read`/`skill.search`), not dumped. Gates on the **binary** `navigated`; the token `disclosure_ratio` is a **non-gating diagnostic** (it's flappy — see `ANALYSIS.md`).

Per-skill verdict (`skillbench/scoring.py:per_skill_verdict`): a skill is READY only if no gating row
fails **and** at least one LLM group produced a row for it (else UNMEASURED — "absence of measurement
is not readiness"). `compare.py` runs a surface-mode A/B (`off` / `deterministic` / `llm@N`) to tune
disclosure vs token cost.

Datasets: `benchmarks/skillbench/dataset/skills/*/SKILL.md` (fixtures) +
`benchmarks/skillbench/dataset/scenarios.jsonl` (cases — categories: clear, paraphrase, distractor,
eager, context_vars, near_neighbor, refusal, over_activation, skill_switch, navigation).
`benchmarks/skillbench/ANALYSIS.md` is the worked review of what the bench discriminates and the
highest-value runtime fixes it points at — read it when optimizing skillbench.

## Reports
Live benches with `--report` write a self-contained HTML page (`results/<bench>.html` or
`report.html`) via `benchmarks/_shared/report.py:write_consolidated` and the interactive viewer
(`agent_sdk/viewer.py`). Open it to inspect per-turn timelines, flow routing, lobe activation,
context provenance, tool calls, and the optimization hotspots (`ProbeRecord.hints`).
