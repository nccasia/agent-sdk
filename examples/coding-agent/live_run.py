"""Live evaluation against a real LLM on a REAL (large) repository.

Two modes:

  # 1) Understand a large codebase — explore + answer (no edits). Point --root at
  #    any repo; the agent navigates with glob/grep/read across hundreds of files.
  python live_run.py "How does the agent_sdk engine drive one turn? Trace it from \
PreactAgent.query to the answer, citing the key files/functions." \
      --root ../../agent_sdk

  # 2) Make a change in a temp sandbox (explore → plan → implement → verify):
  python live_run.py            # default sandbox task

It loads the LLM config from the repo ``.env`` (MiniMax), streams the live tool
trace, and prints how many tool calls it took + token usage — the "hundreds of
tool uses" the agent sustains via PreAct.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))


def _load_env(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_env(os.path.join(REPO_ROOT, ".env"))

from agent_sdk.clients import AnthropicClient  # noqa: E402
from agent_sdk.events import StageStart, TextDelta, ToolCall, ToolResult  # noqa: E402

from coding_agent.agent import build_coding_agent  # noqa: E402
from coding_agent.fakes import CALCULATOR_PY, TEST_CALCULATOR_PY  # noqa: E402


async def _drive(agent, task: str, *, show_results: bool) -> tuple[int, object]:
    print(f"task: {task}\n")
    t0 = time.time()
    tool_calls = 0
    async for ev in agent.act(task):
        match ev:
            case StageStart(_flow, stage):
                print(f"\n── {stage} ──")
            case ToolCall(_id, name, inp):
                tool_calls += 1
                print(f"  [{tool_calls:>3}] → {name}({str(inp)[:90]})")
            case ToolResult(_id, name, out):
                if show_results:
                    first = out.splitlines()[0][:100] if out else ""
                    print(f"        ← {first}")
            case TextDelta(text):
                if text.strip():
                    print(f"  {text[:1500]}")
    elapsed = time.time() - t0
    print(f"\n[perf] wall={elapsed:.1f}s · tool_calls={tool_calls}")
    return tool_calls, (agent.last_trace and agent.last_trace.usage)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?", default="", help="a question about the repo (mode 1)")
    ap.add_argument("--root", default=None, help="repo to work in (default: the agent_sdk package)")
    args = ap.parse_args()

    model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-6")
    if not (os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")):
        print("No ANTHROPIC_AUTH_TOKEN/ANTHROPIC_API_KEY in env (.env not loaded?)")
        return 2
    print(f"model: {model}  ({os.environ.get('ANTHROPIC_BASE_URL', 'default')})")

    if args.question:
        # Mode 1 — understand a real, large repository.
        root = os.path.abspath(args.root or os.path.join(REPO_ROOT, "packages", "agent-sdk", "agent_sdk"))
        nfiles = sum(len(fs) for _, _, fs in os.walk(root))
        print(f"repo: {root}  (~{nfiles} files)\n")
        agent = build_coding_agent(root, client=AnthropicClient(model))
        try:
            calls, usage = await _drive(agent, args.question, show_results=False)
        except Exception as exc:
            print(f"\n[ERROR] {type(exc).__name__}: {exc}")
            return 1
        if usage:
            print(f"[usage] in={usage.input_tokens} out={usage.output_tokens} ~${usage.estimated_cost}")
        return 0

    # Mode 2 — make a change in a temp sandbox.
    with tempfile.TemporaryDirectory() as root:
        with open(os.path.join(root, "calculator.py"), "w") as f:
            f.write(CALCULATOR_PY)
        with open(os.path.join(root, "test_calculator.py"), "w") as f:
            f.write(TEST_CALCULATOR_PY)
        agent = build_coding_agent(root, client=AnthropicClient(model))
        task = "add a multiply function to calculator.py and add a test for it"
        try:
            await _drive(agent, task, show_results=True)
        except Exception as exc:
            print(f"\n[ERROR] {type(exc).__name__}: {exc}")
            return 1
        print("\n── verification (independent of the agent) ──")
        src = open(os.path.join(root, "calculator.py")).read()
        print(f"multiply present: {'def multiply' in src}")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"], cwd=root, capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": root},
        )
        print(f"pytest exit: {proc.returncode}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
