# coding-agent-bench — codebase-understanding stress test

The flagship task pushes `agent_sdk` to its edges: **understand a whole codebase
and write its architecture document.** One request fans out across a four-stage
flow — **survey → plan → investigate → document** — that exercises every component
together: intent routing, multi-stage flows, long agentic loops with Funnel
ReAct, **memory-backed findings aggregation**, and file writing. It renders **one**
self-contained HTML report.

## Two tiers, one HTML

- **scenarios** (free, no-LLM) — assert routing: the understanding request
  resolves to the `understand` flow + its `survey→plan→investigate→document`
  pipeline, while change/question requests route elsewhere. `agent.inspect`, zero tokens.
- **probe** (real behavior) — runs the *whole* pipeline and captures the engine
  internals. The default probe is deterministic but drives the **real filesystem**
  (a scripted model that surveys, reads the code, saves each finding to memory,
  then aggregates them into a real `ARCHITECTURE.md`); `--live` runs it for real
  (MiniMax) against a large codebase (the `agent_sdk` package, or `--target <dir>`).

The probes render into `results/coding-agent-bench.html` — the **polished
benchmark viewer** (`benchmarks/_shared/viewer.html`, reused as an SDK asset:
`agent_sdk/assets/viewer.html`) fed the SDK's own trace. Its panels (Timeline ·
Flow · Lobes · Context · Reasoning · **Prompt** · Efficiency · Optimize · Raw
JSON) render from the engine's real activation + per-hop ReAct capture. The
**Prompt** panel shows the exact bytes sent to the LLM per stage, **coloured by
source lobe/section** (the engine records `system_prompt` + provenance
`system_segments` + the per-hop `messages`). The scenario gate is terminal +
`.json` (the structural CI gate). A rendered close-up of the coloured prompt is
`results/prompt-provenance.png`.

## Run

```bash
cd packages/agent-sdk/benchmarks/coding-agent-bench

python run.py                          # scenarios + deterministic pipeline probe
python run.py --live                   # also understand the agent_sdk package, live
python run.py --live --target <dir>    # understand a specific repo (writes ARCHITECTURE.md there)
```

Exit code is non-zero if any scenario fails (CI gate).

## The SDK surfaces it uses (clean + simple)

```python
from agent_sdk import Harness, Scenario, probe, write_viewer

report = await Harness(agent).run([Scenario(input="…", expect_path="feature")])
rec = await probe(agent, "add a multiply function")
write_viewer("results/report.html", [rec], label="coding-agent-bench")
```

- `Harness` / `Scenario` / `Report` — `agent_sdk/bench.py`
- `probe` / `ProbeRecord` — `agent_sdk/probe.py`
- `write_viewer` / `render_viewer_html` / `to_viewer_record` — `agent_sdk/viewer.py`
  (reuses the polished `benchmarks/_shared/viewer.html`)
- `render_html` / `write_html` — `agent_sdk/report.py` (a lightweight static
  alternative report, still available)

## Latest result

`results/coding-agent-bench.html` — 5/5 scenarios pass (100% path accuracy + lobe
recall). The deterministic probe drives the full `survey → plan → investigate →
document` pipeline (`list_dir → glob → read_file → memory(remember) → … →
memory(recall) → write_file`) and **writes a real `ARCHITECTURE.md`** from
memory-aggregated findings — proving routing + multi-stage flow + Funnel-ReAct
loops + memory aggregation + file writing all working together. `--live` does the
same against the real, large `agent_sdk` package.
