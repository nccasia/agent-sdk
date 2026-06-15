# statelessbench — METHOD

## What it proves

The SDK runs **stateless**: a process holds only the immutable agent *config*; **all** per-session
state — conversation **and** universal working memory — lives in one JSON snapshot
(`SessionState.to_json`). So a worker is just a queue consumer:

```
request → find session id → load the one JSON from the one store → run turn → respond → offload (save)
```

Any replica serves any session; a restart loses nothing; concurrent sessions never bleed.

## Surface under test (the snapshot/serve API)

- `PreactAgent.run_snapshot(input, snapshot) -> (result, snapshot)` — the easy pure stateless turn.
- `SessionState.{to_json,from_json}` + `SNAPSHOT_VERSION` — the one JSON, versioned + tolerant.
- `SessionState.memory` — universal-memory snapshot carried with the session; `MemoryStore.{to_json,restore}`.
- `Session.save` / `SessionStore.save` (InMemory · Redis · SQL) — whole-state offload.
- `serve.AgentWorker(agent_factory=…, store=…)` + `Job.session_id` — the pooled, store-bound queue worker.

## Modes (all FREE / deterministic — FakeClient; memory is driven by auto-establish from the input)

| mode | gate |
|---|---|
| `snapshot` | a fact stored on turn 1 survives a hop to a **fresh** agent on turn 2 (memory + history) |
| `store` | a `SessionStore` (SQLite) round-trips the **whole** state; a new agent resumes on the same id |
| `worker` | jobs carry **only** a `session_id`; the worker binds it to one store, loads/runs/offloads, **resumes by id** |
| `isolation` | a pooled `AgentWorker` runs N sessions concurrently (pool < N, so agents are reused) — **no cross-session bleed** |
| `spec` | `agent.spec().to_json()` → `from_json` rebuilds a byte-identical config (the "init from JSON" half) |
| `schema` | the snapshot is versioned and `from_json` tolerates unknown/missing keys → **extensible** without migration |

## Gate

Deterministic: every mode's checks must pass → `READY` (exit 0). No LLM tier — this measures
plumbing, not answer quality.

## Run

```bash
python benchmarks/statelessbench/run.py --report --label base
```
