"""Offline, deterministic demo: the coding agent edits real files + runs pytest.

No network needed — a scripted ``FakeCodingModel`` drives the turn, but every
filesystem and subprocess action is real. Run it:

    python demo.py

It creates a temp sandbox repo (calculator.py + tests), asks the agent to "add a
multiply function and a test", and prints the live event stream while the agent
actually edits the files and runs the real test suite.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_sdk.events import StageStart, TextDelta, ToolCall, ToolResult  # noqa: E402

from coding_agent.agent import build_coding_agent  # noqa: E402
from coding_agent.fakes import CALCULATOR_PY, TEST_CALCULATOR_PY, make_fake_client  # noqa: E402

TASK = "add a multiply function to calculator.py and a test for it"


def _seed(root: str) -> None:
    with open(os.path.join(root, "calculator.py"), "w") as f:
        f.write(CALCULATOR_PY)
    with open(os.path.join(root, "test_calculator.py"), "w") as f:
        f.write(TEST_CALCULATOR_PY)


async def main() -> None:
    with tempfile.TemporaryDirectory() as root:
        _seed(root)
        agent = build_coding_agent(root, client=make_fake_client())

        snap = agent.inspect(TASK)
        print(f"task: {TASK}")
        print(f"routed flow: {snap.path[0]}  →  {' → '.join(snap.flow)}\n")

        async for ev in agent.act(TASK):
            match ev:
                case StageStart(_flow, stage):
                    print(f"\n── {stage} ──")
                case ToolCall(_id, name, inp):
                    print(f"  → {name}({str(inp)[:70]})")
                case ToolResult(_id, name, out):
                    print(f"  ← {name}: {out.splitlines()[0][:90] if out else ''}")
                case TextDelta(text):
                    if text.strip():
                        print(f"  {text}")

        print("\n── result on disk ──")
        with open(os.path.join(root, "calculator.py")) as f:
            print(f.read())
        print("files:", sorted(os.listdir(root)))
        t = agent.last_trace
        print(f"[usage] in={t.usage.input_tokens} out={t.usage.output_tokens}")


if __name__ == "__main__":
    asyncio.run(main())
