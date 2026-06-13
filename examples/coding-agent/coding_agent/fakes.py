"""A deterministic fake "model" that drives a realistic coding session.

Lets the example run end-to-end in CI with no network while still exercising the
*real* filesystem + subprocess tools: the agent actually edits files on disk and
runs the real test suite. A live run swaps this for ``AnthropicClient(...)``.

The scripted task is: *"add a multiply function to calculator.py and a test"*.
The handler branches on the engine's ``stage`` and the per-stage hop to emit the
tool calls + final text a competent engineer would, exercising explore → plan →
implement → verify → summarize.
"""

from __future__ import annotations

from typing import Any

from agent_sdk.clients import FakeClient
from coding_agent.tools import PYTEST_CMD

# Initial sandbox contents (the demo/test writes these to a temp dir).
CALCULATOR_PY = '''"""A tiny calculator."""


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b
'''

TEST_CALCULATOR_PY = '''from calculator import add, subtract


def test_add():
    assert add(2, 3) == 5


def test_subtract():
    assert subtract(5, 2) == 3
'''

_SUBTRACT = "def subtract(a, b):\n    return a - b\n"
_MULTIPLY = "\n\ndef multiply(a, b):\n    return a * b\n"
_NEW_TEST = '''from calculator import multiply


def test_multiply():
    assert multiply(2, 4) == 8
'''


class FakeCodingModel:
    """Stateful handler for ``FakeClient`` — one realistic 'add multiply' session."""

    def __init__(self) -> None:
        self._hops: dict[str, int] = {}

    def __call__(self, stage: str, system: Any, messages: Any, tools: Any) -> Any:
        n = self._hops.get(stage, 0)
        self._hops[stage] = n + 1

        if stage == "explore":
            if n == 0:
                return {"text": "Let me see the layout.",
                        "tools": [{"name": "LS", "input": {"path": "."}}]}
            if n == 1:
                return {"text": "Reading the calculator module.",
                        "tools": [{"name": "Read", "input": {"file_path": "calculator.py"}}]}
            return "calculator.py defines add and subtract. I'll add multiply plus a test."

        if stage == "plan":
            return ("1. Add multiply(a, b) to calculator.py after subtract.\n"
                    "2. Add test_multiply in a new test_multiply.py.")

        if stage == "implement":
            if n == 0:
                return {"text": "Adding multiply().",
                        "tools": [{"name": "Edit", "input": {
                            "file_path": "calculator.py", "old_string": _SUBTRACT,
                            "new_string": _SUBTRACT + _MULTIPLY}}]}
            if n == 1:
                return {"text": "Adding a test.",
                        "tools": [{"name": "Write", "input": {
                            "file_path": "test_multiply.py", "content": _NEW_TEST}}]}
            return "Implemented multiply() and added test_multiply.py."

        if stage == "verify":
            if n == 0:
                return {"text": "Running the tests.",
                        "tools": [{"name": "Bash", "input": {"command": PYTEST_CMD}}]}
            return "The full test suite passes."

        # summarize (single) and answer flow
        return ("Added `multiply(a, b)` to calculator.py (after `subtract`) and a new "
                "test_multiply.py. Ran the suite with pytest — all tests pass.")


def make_fake_client() -> FakeClient:
    """A FakeClient wired to the FakeCodingModel handler (effectively unbounded)."""
    return FakeClient([FakeCodingModel()] * 500)


_ARCH_MD = """# Architecture — tiny calculator

## Overview
A minimal arithmetic library: pure functions over numbers, with a matching test
suite. No external dependencies.

## Subsystems
- **calculator.py** — the core operations (`add`, `subtract`). Each is a pure
  function of two numbers.
- **test_calculator.py** — the test suite covering each operation.

## How it fits together
Callers import the operation functions directly from `calculator`. The tests
import the same functions and assert their results. There is no runtime wiring,
config, or state — the public surface is the operation functions.

## Entry points
`from calculator import add, subtract`
"""


class FakeUnderstandModel:
    """Drives the codebase-understanding flow (survey → plan → investigate →
    document) on the calculator sandbox: maps the structure, plans, reads the
    code, SAVES findings to memory, then aggregates them into ARCHITECTURE.md."""

    def __init__(self) -> None:
        self._hops: dict[str, int] = {}

    def __call__(self, stage: str, system: Any, messages: Any, tools: Any) -> Any:
        n = self._hops.get(stage, 0)
        self._hops[stage] = n + 1

        if stage == "survey":
            if n == 0:
                return {"text": "Mapping the repo.",
                        "tools": [{"name": "LS", "input": {"path": "."}}]}
            if n == 1:
                return {"text": "Finding the Python files.",
                        "tools": [{"name": "Glob", "input": {"pattern": "**/*.py"}}]}
            return ("Structure: a flat package — calculator.py (the operations) and "
                    "test_calculator.py (the tests). One subsystem to study.")

        if stage == "plan":
            return ("Plan: 1) study calculator.py (the operations). 2) note the test "
                    "coverage. Save each finding to memory, then write ARCHITECTURE.md.")

        if stage == "investigate":
            if n == 0:
                return {"text": "Reading the operations.",
                        "tools": [{"name": "Read", "input": {"file_path": "calculator.py"}}]}
            if n == 1:
                return {"text": "Noting the finding.",
                        "tools": [{"name": "memory", "input": {
                            "action": "remember", "scope": "conversation",
                            "key": "finding:operations",
                            "value": "calculator.py defines pure add(a,b) and subtract(a,b)."}}]}
            if n == 2:
                return {"text": "Checking the tests.",
                        "tools": [{"name": "Read", "input": {"file_path": "test_calculator.py"}}]}
            if n == 3:
                return {"text": "Noting the test finding.",
                        "tools": [{"name": "memory", "input": {
                            "action": "remember", "scope": "conversation",
                            "key": "finding:tests",
                            "value": "test_calculator.py covers add and subtract."}}]}
            return "Investigated both files; findings saved to memory."

        if stage == "document":
            if n == 0:
                return {"text": "Recalling all findings.",
                        "tools": [{"name": "memory", "input": {
                            "action": "recall", "scope": "conversation", "query": "finding"}}]}
            if n == 1:
                return {"text": "Writing the architecture document.",
                        "tools": [{"name": "Write", "input": {
                            "file_path": "ARCHITECTURE.md", "content": _ARCH_MD}}]}
            return ("Wrote ARCHITECTURE.md — an overview, the calculator.py + "
                    "test_calculator.py subsystems, how they fit, and the entry points.")

        return "Done."


def make_understand_client() -> FakeClient:
    """A FakeClient wired to the FakeUnderstandModel handler."""
    return FakeClient([FakeUnderstandModel()] * 500)
