"""A real SQLite analytics fixture + a read-only ``sql`` tool.

``build_db()`` creates an in-memory ``sales`` table with deterministic data (4 regions × 4
products × 6 months = 96 rows, with a built-in month-over-month growth trend), so the example
runs genuine analytics with reproducible results. ``SqlToolRuntime`` exposes one ``sql`` tool
(SELECT-only) that the spawned subagents use to run their slice of the analysis.
"""

from __future__ import annotations

import sqlite3

REGIONS = ["North", "South", "East", "West"]
# (product, category, unit_price)
PRODUCTS = [
    ("Widget", "Hardware", 20),
    ("Gadget", "Hardware", 35),
    ("Gizmo", "Accessory", 50),
    ("Doohickey", "Accessory", 15),
]
MONTHS = ["2024-01", "2024-02", "2024-03", "2024-04", "2024-05", "2024-06"]


def build_db() -> sqlite3.Connection:
    """An in-memory sales DB with deterministic, trend-bearing data."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE sales (id INTEGER PRIMARY KEY, region TEXT, product TEXT, "
        "category TEXT, units INTEGER, revenue REAL, order_date TEXT)"
    )
    rid = 0
    for ri, region in enumerate(REGIONS):
        for pi, (product, category, price) in enumerate(PRODUCTS):
            for mi, month in enumerate(MONTHS):
                rid += 1
                units = 10 + (ri * 7 + pi * 5 + mi * 3) % 40
                revenue = round(units * price * (1 + mi * 0.06), 2)  # ~6% monthly growth
                conn.execute(
                    "INSERT INTO sales VALUES (?,?,?,?,?,?,?)",
                    (rid, region, product, category, units, revenue, f"{month}-15"),
                )
    conn.commit()
    return conn


class SqlToolRuntime:
    """A ``ToolRuntime`` exposing one read-only ``sql`` tool over the analytics DB."""

    name = "sql"

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get_tool_specs(self) -> list[dict]:
        return [
            {
                "name": "sql",
                "description": (
                    "Run a read-only SQL SELECT against the analytics database and get the rows "
                    "back as a table. Schema: sales(region, product, category, units, revenue, "
                    "order_date). One query per call."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "a SELECT query"}},
                    "required": ["query"],
                },
            }
        ]

    async def call_tool(self, name, inp, retrieved_chunks=None, already_read=None) -> str:
        q = str(inp.get("query") or "").strip()
        if not q.lower().startswith("select"):
            return "Error: only read-only SELECT queries are allowed."
        try:
            cur = self._conn.execute(q)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        except Exception as exc:  # surface the DB error to the model
            return f"Error: {exc}"
        lines = [" | ".join(cols)]
        for r in rows[:20]:
            lines.append(" | ".join(str(x) for x in r))
        return "rows:\n" + "\n".join(lines)
