"""Offline, deterministic demo: the agent PLANS 3 SQL analyses, FANS OUT a subagent each, FANS IN.

No network — a scripted model drives the turn, but every SQL query is REAL (it runs against the
in-memory SQLite fixture). Run it:

    python demo.py
    python demo.py --report   # also write report.html (the Prompt tab shows the rendered plan)

The agent routes the multi-part analytics question to the `plan` flow: it writes a 3-step todo list
with `TodoWrite` (top products / revenue by region / monthly trend), the supervisor sees the steps
are independent and picks **fanout**, the engine runs one isolated subagent per todo (each runs its
own SQL), and the fan-in step combines the three results into an executive summary.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_sdk import probe  # noqa: E402
from agent_sdk.events import Final, StageStart, ToolCall, ToolResult  # noqa: E402
from agent_sdk.viewer import write_viewer  # noqa: E402

from analytics.agent import build_analytics_agent  # noqa: E402
from analytics.fakes import make_fake_client  # noqa: E402
from analytics.fixture import build_db  # noqa: E402

QUESTION = (
    "Give me a 2024 sales review in three parts — (1) the top products by revenue, (2) total "
    "revenue by region, and (3) the monthly revenue trend — then combine them into an executive "
    "summary."
)


def _planned(rec) -> int:
    sizes = [
        len((tc.get("input") or {}).get("todos") or [])
        for tc in rec.tool_calls
        if tc.get("name") == "TodoWrite"
    ]
    return max(sizes, default=0)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--report",
        action="store_true",
        help="also write report.html (the Prompt tab shows the rendered plan)",
    )
    a = ap.parse_args()

    conn = build_db()
    agent = build_analytics_agent(conn, client=make_fake_client())

    if a.report:
        rec = await probe(agent, QUESTION, label="sales-review")
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report.html")
        write_viewer(out, [rec], label="analytics planning demo")
        subs = len(rec.blackboard.get("todos_results") or [])
        print(f"Q: {QUESTION}\n")
        print(
            f"routed to '{rec.flow}', planned {_planned(rec)} steps → "
            f"{rec.blackboard.get('plan_structure', '?')} → {subs} subagents → {out}"
        )
        print("open it and click the 'Prompt' tab to see the rendered plan per hop.")
        return

    print(f"Q: {QUESTION}\n")
    print("── live event stream ─────────────────────────────────────────")
    async for ev in agent.act(QUESTION):
        if isinstance(ev, StageStart):
            print(f"\n▶ stage: {ev.stage}")
        elif isinstance(ev, ToolCall):
            inp = ev.input or {}
            if ev.name == "TodoWrite":
                steps = [t.get("content") for t in inp.get("todos") or []]
                print(
                    f"   → TodoWrite({len(steps)} steps: {', '.join(str(s)[:24] for s in steps)})"
                )
            else:
                print(f"   → {ev.name}({str(inp.get('query') or '')[:64]})")
        elif isinstance(ev, ToolResult):
            first = str(ev.output or "").splitlines()[:1]
            print(f"     ← {(first[0] if first else '')[:64]}")
        elif isinstance(ev, Final):
            print("\n── final answer ─────────────────────────────────────────────")
            print(ev.result.text)
            print(f"\nstatus: {ev.result.status}")


if __name__ == "__main__":
    asyncio.run(main())
