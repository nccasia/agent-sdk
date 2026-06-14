# toolbench

Certifies the SDK's **tool-use concepts** — how a PreAct agent defines, exposes, routes, and runs
tools at scale. Ported from the monorepo `benchmarks/toolbench` and re-authored on the agent-sdk
public surface (leaf-pure: imports only `agent_sdk.*` + `benchmarks._shared`). See `METHOD.md` for the
optimization levers + gates.

```bash
python benchmarks/toolbench/run.py                  # free tier only (no provider) — 14 deterministic checks
python benchmarks/toolbench/run.py --live --report  # + the live agentic loop, write results/toolbench.html
```

Exits `0` iff READY. The free tier needs no creds; the live tier auto-loads `.env` via
`_shared/provider.py`.

## What it exercises (the concepts)
- **`@tool` / `Tool` / `FunctionToolRuntime`** — docstring→description, signature→JSON schema,
  `missing_required` model-actionable errors, `invoke` stringification (`spec` mode).
- **`ToolSelectLobe`** — adaptive exposure: essentials firewall always kept, non-essentials scored by
  relevance, `below_floor`/`max_tools` drops, inert at parity (`select` mode).
- **`CompositeToolRuntime` + embedded `MCPToolRuntime`** — multi-runtime dispatch, spec dedup, MCP
  discovery + call over the in-process transport seam, external-tools never-drop guard (`composite` mode).
- **`tool_loop`** — the live agentic loop: calls a tool, feeds the result back, terminates within
  bounded hops (`loop` mode, `--live`).

## Feedback loop
Plugs into the standard ratchet (`improve/best.json` + `wave-NNN/`, `improve_cli.py`) and the
`benchmarks/loop` ladder. Diagnose a failing gate → tune the lever in `METHOD.md` → re-bench → keep
only if it ratchets up.
