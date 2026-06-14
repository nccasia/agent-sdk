"""Live run: a real model fans out 3 SQL subagents over the SQLite fixture, then aggregates.

Needs a provider token (set ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY, or pass --model). Unlike
demo.py (scripted), here the model decides the sub-analyses, writes the SQL, and composes the
summary itself — the SDK only routes, fans out (isolated), and grounds.

    python main.py
    python main.py --model claude-opus-4-8
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_sdk.clients import AnthropicClient  # noqa: E402
from agent_sdk.events import Final, StageStart, ToolCall, ToolResult  # noqa: E402

from analytics.agent import build_analytics_agent  # noqa: E402
from analytics.fixture import build_db  # noqa: E402

QUESTION = (
    "Split this 2024 sales review into three independent analyses and run each in its own "
    "subagent: the top products by revenue, total revenue by region, and the monthly revenue "
    "trend — then combine them into an executive summary."
)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8"))
    a = ap.parse_args()

    conn = build_db()
    agent = build_analytics_agent(conn, client=AnthropicClient(a.model))

    print(f"Q: {QUESTION}\n")
    async for ev in agent.act(QUESTION):
        if isinstance(ev, StageStart):
            print(f"\n▶ stage: {ev.stage}")
        elif isinstance(ev, ToolCall):
            inp = ev.input or {}
            arg = inp.get("task") or inp.get("query") or ""
            print(f"   → {ev.name}({str(arg)[:80]})")
        elif isinstance(ev, ToolResult):
            first = str(ev.output or "").splitlines()[:1]
            print(f"     ← {(first[0] if first else '')[:80]}")
        elif isinstance(ev, Final):
            print("\n── final answer ─────────────────────────────────────────────")
            print(ev.result.text)
            print(f"\nstatus: {ev.result.status}")


if __name__ == "__main__":
    asyncio.run(main())
