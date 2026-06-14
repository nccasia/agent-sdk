"""Unit — the task_rail render lobe, in isolation (a fake scratchpad, no engine)."""

from __future__ import annotations

from types import SimpleNamespace

from agent_sdk.plugins.tasks.lobes import TaskRailLobe


class _SP:
    def __init__(self, data):
        self._d = data

    def as_list(self, k):
        v = self._d.get(k)
        return list(v) if isinstance(v, list) else ([] if v is None else [v])


def _ctx(data):
    return SimpleNamespace(scratchpad=_SP(data))


def test_renders_rail_with_done_status_and_deps():
    ctx = _ctx(
        {
            "todos": [
                {"id": "t0", "input": "fetch revenue"},
                {"id": "t1", "input": "compute profit", "deps": ["t0"]},
            ],
            "todos_results": [{"label": "t0", "result": "200"}],
        }
    )
    out = TaskRailLobe().prompt(ctx)
    assert len(out) == 1
    text = out[0].text
    assert "[x] t0: fetch revenue" in text  # done (has a result)
    assert "[ ] t1: compute profit (needs t0)" in text  # open + dependency shown


def test_empty_rail_contributes_nothing():
    assert TaskRailLobe().prompt(_ctx({})) == []
    assert TaskRailLobe().prompt(SimpleNamespace(scratchpad=None)) == []
