"""CLI for the coding agent.

    # live (needs ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY in env):
    python main.py --root /path/to/repo "add a multiply function to calculator.py"

    # routing probe only — no LLM, no edits:
    python main.py --root . --inspect "fix the failing test in parser.py"

    # deterministic offline demo (real fs edits in a temp sandbox, scripted model):
    python demo.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_sdk.clients import AnthropicClient  # noqa: E402
from agent_sdk.events import StageStart, TextDelta, ToolCall, ToolResult  # noqa: E402

from coding_agent.agent import build_coding_agent  # noqa: E402


async def run(root: str, task: str, model: str, inspect_only: bool) -> int:
    agent = build_coding_agent(root, client=AnthropicClient(model))

    if inspect_only:
        snap = agent.inspect(task)
        print(f"flow:  {snap.path[0]}  (score {snap.path[1]})")
        print(f"stages: {' → '.join(snap.flow)}")
        print("lobes activated:", [lb["id"] for lb in snap.lobes if lb["activated"]])
        return 0

    print(f"task: {task}\nrepo: {os.path.abspath(root)}\n")
    async for ev in agent.act(task):
        match ev:
            case StageStart(_flow, stage):
                print(f"\n── stage: {stage} ──")
            case ToolCall(_id, name, inp):
                print(f"  → {name}({_short(inp)})")
            case ToolResult(_id, name, out):
                print(f"  ← {name}: {out.splitlines()[0][:100] if out else ''}")
            case TextDelta(text):
                if text.strip():
                    print(text)
    result = agent.last_trace
    if result:
        u = result.usage
        print(f"\n[usage] in={u.input_tokens} out={u.output_tokens} ~${u.estimated_cost}")
    return 0


def _short(inp: dict | None) -> str:
    s = str(inp or {})
    return s[:80] + "…" if len(s) > 80 else s


def main() -> int:
    p = argparse.ArgumentParser(description="A coding agent on agent_sdk.")
    p.add_argument("task", help="what you want done")
    p.add_argument("--root", default=".", help="repository root (default: cwd)")
    p.add_argument("--model", default="claude-opus-4-6", help="Anthropic model id")
    p.add_argument("--inspect", action="store_true", help="routing probe only (no LLM)")
    args = p.parse_args()
    return asyncio.run(run(args.root, args.task, args.model, args.inspect))


if __name__ == "__main__":
    raise SystemExit(main())
