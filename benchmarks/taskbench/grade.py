"""Grade a submitted answer against ground truth computed from reference SQL.

Ground truth is the set of cells returned by a task's ``answer_sql`` run against the
*same* seeded DB — so nothing is hand-entered. A fact is matched if it appears in the
agent's free-text answer: strings by case-insensitive containment, numbers by
relative/absolute tolerance. answer_correct ⇔ every ground-truth fact is present.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3

_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def ground_truth(conn: sqlite3.Connection, answer_sql: str) -> list:
    """Flatten the reference query's result cells into a list of facts."""
    cur = conn.execute(answer_sql)
    return [cell for row in cur.fetchall() for cell in row]


def _numbers(text: str) -> list[float]:
    out = []
    for m in _NUM_RE.findall(text or ""):
        with contextlib.suppress(ValueError):
            out.append(float(m.replace(",", "")))
    return out


def _num_match(target: float, answer: str) -> bool:
    tol = max(abs(target) * 0.03, 1.0)  # 3% or ±1 (counts), whichever is larger
    return any(abs(n - target) <= tol for n in _numbers(answer))


def grade_answer(submitted: str | None, facts: list) -> tuple[bool, list[dict]]:
    """Return (all_facts_present, per-fact detail)."""
    ans = submitted or ""
    low = ans.lower()
    detail = []
    for f in facts:
        if isinstance(f, bool):
            continue
        if isinstance(f, (int, float)):
            ok = _num_match(float(f), ans)
        else:
            s = str(f).strip()
            ok = bool(s) and s.lower() in low
        detail.append({"fact": f, "ok": ok})
    return (all(d["ok"] for d in detail) if detail else False), detail
