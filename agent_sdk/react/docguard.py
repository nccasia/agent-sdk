"""``DocWriteGuard`` — heavy-output write discipline (a tool-call filter).

A long-running agent under a capable model sometimes writes the *same* artifact
several times in one step (a full rewrite, then an append, then another rewrite)
or smuggles a write into a read-only step via a ``bash`` heredoc — wasting output
tokens and breaking stage discipline (observed live: an ``ARCHITECTURE.md`` written
3× during a *survey* stage).

This guard is a tool-call filter — ``(stage_id, name, input) -> str | None`` — for
the engine's ``_tool_filters`` seam. It is generic over tool names (you pass the
write-tool names + the read-only stage ids):

* A **second full write of the same target path within a stage** is intercepted;
  the model gets a tool result steering it to edit/append instead of rewriting.
* A **first write of a path already produced in an earlier stage** is intercepted
  too (cross-stage), so a multi-stage flow doesn't re-derive the same deliverable
  from scratch each stage — it's steered to read + edit instead.
* A **write (the write tool *or* a ``bash`` heredoc) in a read-only stage** is
  refused — the per-stage tool allowlist only hides the spec, so the guard is the
  real enforcement against a write the model calls from its training priors.

``record_only=True`` keeps it measure-only (logs ``events`` without intercepting) —
the "measure, then enforce" path. ``events`` is always populated for telemetry.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["DocWriteGuard"]

# `cat > f`, `cat >> f`, `tee f`, `> f` redirections in a shell command.
_BASH_WRITE_RE = re.compile(r"(^|[\s|&;])(cat\s+>>?|tee(\s+-a)?\s|>>?)\s*([^\s|&;<>]+)")


class DocWriteGuard:
    """A tool-call filter that bounds redundant heavy writes + read-only writes."""

    def __init__(
        self,
        *,
        write_tools: tuple[str, ...] = ("write_file",),
        path_keys: tuple[str, ...] = ("path", "file", "filename", "file_path"),
        bash_tool: str = "bash",
        readonly_stages: tuple[str, ...] = (),
        record_only: bool = False,
    ) -> None:
        self.write_tools = set(write_tools)
        self.path_keys = path_keys
        self.bash_tool = bash_tool
        self.readonly_stages = set(readonly_stages)
        self.record_only = record_only
        self._writes: dict[tuple[str, str], int] = {}  # (stage, path) -> count
        self._path_writes: dict[str, int] = {}  # path -> count across all stages
        self.events: list[dict] = []  # {stage, path?, action} — telemetry

    def _target_path(self, name: str, inp: dict) -> str | None:
        if name in self.write_tools:
            for k in self.path_keys:
                v = inp.get(k)
                if isinstance(v, str) and v:
                    return v
        return None

    def __call__(self, stage_id: str, name: str, inp: dict[str, Any]) -> str | None:
        # 1) A bash write inside a read-only stage — refuse (heredoc bypass guard).
        if name == self.bash_tool and stage_id in self.readonly_stages:
            cmd = str(inp.get("command", "") or "")
            m = _BASH_WRITE_RE.search(cmd)
            if m:
                target = m.group(4)
                self.events.append(
                    {"stage": stage_id, "path": target, "action": "blocked_readonly_write"}
                )
                if not self.record_only:
                    return (
                        f"Refused: '{stage_id}' is a read-only step — do not write files here "
                        f"(attempted to write {target!r}). Explore/read only; defer writing to a "
                        "later step that has a write tool."
                    )
            return None

        path = self._target_path(name, inp)
        if path is None:
            return None

        # 2) A write *tool* inside a read-only stage — refuse outright (the per-stage
        # tool allowlist only hides the spec; the runtime would still execute a tool
        # the model calls from its priors, so the guard is the actual enforcement).
        if stage_id in self.readonly_stages:
            self.events.append(
                {"stage": stage_id, "path": path, "action": "blocked_readonly_write"}
            )
            if not self.record_only:
                return (
                    f"Refused: '{stage_id}' is a read-only step — do not write files here "
                    f"(attempted to write {path!r}). Explore/read only; defer writing to a "
                    "later step that has a write tool."
                )
            return None

        # 3) A repeated full write of the same target within a stage — steer to edit.
        key = (stage_id, path)
        self._writes[key] = self._writes.get(key, 0) + 1
        prior_total = self._path_writes.get(path, 0)
        self._path_writes[path] = prior_total + 1
        if self._writes[key] > 1:
            self.events.append({"stage": stage_id, "path": path, "action": "redundant_rewrite"})
            if not self.record_only:
                return (
                    f"Note: {path!r} was already written in this step. Don't rewrite the whole "
                    "file — use an edit/append tool to change only what needs to change, or "
                    "write the complete document once."
                )
        elif prior_total > 0:
            # First write of this path in *this* stage, but it was already produced
            # in an earlier stage — the deliverable shouldn't be re-derived per stage.
            self.events.append(
                {"stage": stage_id, "path": path, "action": "redundant_rewrite_cross_stage"}
            )
            if not self.record_only:
                return (
                    f"Note: {path!r} was already written in an earlier step. Read it and make a "
                    "targeted edit instead of rewriting the whole file from scratch."
                )
        return None
