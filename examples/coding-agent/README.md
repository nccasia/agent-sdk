# coding-agent — a Claude-Code-grade coding agent on `agent_sdk`

A multi-stage, tool-using coding agent built on the SDK that works in a **real,
large** repository: it navigates with glob/grep, reads exact files, plans, edits,
runs the test suite, and answers deep questions about the code — sustaining
**hundreds of tool calls** in a bounded context via **PreAct**. Built
entirely on the SDK's public surface.

The whole capability is packaged as **one first-class plugin** — `CodingPlugin`
(`coding_agent/agent.py`) contributes its lobes + stages + flows + tools + the read-only write
guard through the plugin seam, and `build_coding_agent` just mounts it on a bare base network
(`plugins=[CodingPlugin(root)]`). Compose it with other plugins or give it an MCP server it
owns — `build_coding_agent(root, client=…, mcp_servers=[{...}])` / `plugins=[…]` pass straight
through.

**Built for scale:**
- **Claude Code's canonical tools** — `Read` (line numbers + offset/limit), `Write`,
  `Edit` (exact-string), `LS`, `Glob` (`**/*.py`), `Grep`, `Bash` — same names and
  param shapes (`file_path`/`old_string`/…) the model already knows from training, so
  prompts stay terse and accuracy stays high.
- **PreAct** (`funnel=True`) — spent tool observations shrink to hints, so
  long exploration doesn't overflow the window.
- **High hop budgets** (explore 50 · implement 80 · verify 40 · answer 120).
- **Durable memory** — the agent tracks its plan/goals across turns.

See [`EVALUATION.md`](./EVALUATION.md) for a candid assessment of the SDK
(API ergonomics, live MiniMax performance numbers, behavior findings, and
prioritized improvement suggestions) produced by building this.

## Shape

```
request ──▶ recognize flow (free, deterministic)
            ├─ question  → explore → answer
            ├─ quick_fix → explore → implement → verify → summarize
            └─ feature   → explore → plan → implement → verify → summarize
```

- **Plugin** (`CodingPlugin`, `coding_agent/agent.py`): packages everything below as one installable unit — `install(setup)` calls `add_lobe`/`add_stage`/`add_flow`/`add_tool`/`add_tool_filter`.
- **Lobes** (`coding_agent/lobes.py`): `triage`, `explore`, `plan`, `implement`, `verify`, `summarize` — the coding disciplines as context workers.
- **Stages + flows** (`coding_agent/agent.py`): each stage consults a lobe slice + a tool set + a hop budget.
- **Tools** (`coding_agent/tools.py`): `Read`, `Write`, `Edit`, `LS`, `Glob`, `Grep`, `Bash` — Claude Code's canonical names + schemas, over the real workspace on disk.

## Run it

```bash
# from the repo root, with the venv that has agent_sdk installed:

# 1) Offline deterministic demo — real fs edits in a temp sandbox, scripted model:
python packages/agent-sdk/examples/coding-agent/demo.py

# 2) No-LLM routing probe (free, instant):
python packages/agent-sdk/examples/coding-agent/main.py --inspect "fix the failing test"

# 3) Live — UNDERSTAND a large repo (explore + answer, no edits):
python packages/agent-sdk/examples/coding-agent/live_run.py \
    "How does the engine drive one turn? Cite the key files/functions." \
    --root packages/agent-sdk/agent_sdk

# 4) Live — make a change on your repo:
python packages/agent-sdk/examples/coding-agent/main.py \
    --root /path/to/repo "add a multiply function to calculator.py and a test"

# 5) Live sandbox feature demo (loads .env → MiniMax, verifies independently):
python packages/agent-sdk/examples/coding-agent/live_run.py
```

## Latest live result (MiniMax-M2.7, on this repo)

Asked *"What does the engine's agentic tool loop do, and how does PreAct
keep the context bounded? Cite the files/functions."* against the 245-file
`agent_sdk` package — the agent routed to `answer`, navigated with **18 tool
calls** (`LS` → `Glob('**/*.py')` → `Read` ×12 of the relevant files:
`engine.py`, `react/funnel.py`, `engine_context.py`), and produced a cited
architectural explanation. 90s · 86k in-tokens · ~$0.29 — PreAct kept the
context bounded across the run. (Routing fix: a long *question* now goes to
explore→answer, never to the `feature` change flow.)

## Test it (real fs, no network)

```bash
uv --directory packages/agent-sdk run python -m pytest examples/coding-agent/test_coding_agent.py -q
```

The suite asserts the agent routes correctly, **actually edits files on disk**,
**runs the real test suite**, and reports honestly.

## Safety note

`bash` executes arbitrary shell in the workspace (so the agent can run your tests),
and `Write`/`Edit` modify files in place. That is intentional for a
coding agent but **powerful** — run untrusted tasks in a sandbox/container. See
EVALUATION.md finding #6 (a tool-authorization seam is a recommended SDK addition).
