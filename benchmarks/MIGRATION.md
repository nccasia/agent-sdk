# Benchmark migration — monorepo `benchmarks/` → the SDK

Tracks porting the monorepo benchmarks (`<repo-root>/benchmarks/`, built on the **project engine**
`agent_core`/`rag_core`/`BotPolicyInterpreter`) into this standalone SDK. The SDK is a **leaf**
(`tests/test_sdk_isolation.py` forbids `agent_core`/`rag_core`/`arag_core`/`ingest_core`), so a bench
cannot be copied — it must be **re-authored on the agent-sdk public surface**. We port the benches
that test concepts the SDK *has*; the project-only ones stay in the monorepo (their CI + `.converge`
playbooks depend on them, and they test a different subject — the project engine).

**This is a port, not a move:** the monorepo `benchmarks/` is untouched.

## Status

| Monorepo bench | Tests | Category | SDK target | Status |
|---|---|---|---|---|
| **toolbench** | tool exposure/trimming, essentials firewall, agentic loop | SDK-concept | `agent_sdk/tools` (`@tool`/runtimes/MCP/`tool_select`/`tool_loop`) | **ported** → `benchmarks/toolbench/` (leaf-pure, runnable) |
| **attentionbench** | lobe/path activation + context selection under traps/floods | SDK-concept | `agent_sdk/network/activation.py` + `agent_sdk/lobes/` | **ported** → `benchmarks/attentionbench/` (deterministic, runnable) |
| **flowbench** | flow-axis: sequence/customize/handoff/react/fault | SDK-concept | `agent_sdk/flows/` | **ported** → `benchmarks/flowbench/` (deterministic, runnable) |
| **corgictionbech** | metacognition regulator decision table + pinned-step guards | SDK-concept | `agent_sdk/metacognition/` + `agent_sdk/inspection.py` | **ported** → `benchmarks/corgictionbech/` (deterministic, runnable) |
| skillbench | skill activation / uplift / layering | SDK-concept | `agent_sdk/skills/` | already SDK-native (`benchmarks/skillbench/`) — different subject (SDK skills, not bot skills) |
| taskbench | task **execution** quality | SDK-concept (subject differs) | `agent_sdk` task mode | already SDK-native (`benchmarks/taskbench/`) — monorepo tests the production `bot_tasks` backend |
| contextbench | scoped memory: save/forget/recall/scope | partial | `agent_sdk/memory/` | candidate (after a memory-bench design pass) |
| crag | generic engine answer-correctness on an external dataset | partial | `agent_sdk` + an SDK judge | candidate (needs an SDK judge equivalent) |
| agentcore | meta-runner over engine/skill checks | partial | a meta-runner over SDK benches | candidate (the SDK has its own ladder: `benchmarks/loop`) |
| tasksbench | shared task lib behind task/schedule benches | project-only | — | stays in monorepo (production task backend, arq, `app.services`) |
| schedulebench | NL→schedule task assistance | project-only | — | stays in monorepo |
| adminbench | steward mode via `admin.*` MCP tools | project-only | — | stays in monorepo |
| mellobench | `mello.*` task handling (Go worker-mello) | project-only | — | stays in monorepo |
| assistantbench | shipped-assistant final exam (seed bundle, KB, backends) | project-only | — | stays in monorepo |
| funrag | FUNiX bot KB retrieval + faithfulness | project-only | — | stays in monorepo |

## The port recipe (how a bench crosses the seam)
1. **Re-author on the SDK surface** — `PreactAgent` + the relevant `agent_sdk` modules; never
   `agent_core`/`rag_core`. The monorepo `BotPolicyInterpreter` harness becomes a `PreactAgent`.
2. **Conform to the standard** (`_shared/TEMPLATE.md`): `run.py` with free + live modes →
   `compose_verdict`, a `METHOD.md` (lever + metrics + gates), a discriminating `dataset/`.
3. **Use the SDK probe** (`agent_sdk.probe`) for the trace, `write_viewer` for the report — not the
   monorepo `_shared/probe.py` (which imports `agent_core`).
4. **Stay leaf-pure** — `tests/test_sdk_isolation.py` covers `agent_sdk/`; the bench must follow the
   same rule (imports only `agent_sdk.*` + `benchmarks._shared`).
5. **Wire in** — add to `benchmarks/loop/ladder.sh`; the `improve/` ratchet is automatic.

`toolbench` is the worked reference for all of the above. Finish a scaffold by copying
`benchmarks/_template/run.py` and implementing the modes in its `PORT-NOTES.md` (or use the
`bench-scaffold` skill).
