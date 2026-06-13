"""SqlitePlugin — the bench's DOMAIN capability (real SQL over the seeded DB).

The bench EXTENDS the task agent: ``TaskPlugin`` owns *how to drive a task*; this plugin
owns *the domain* — ``db.schema`` / ``db.query`` (read-only) over a live SQLite connection.
No flows/stages/todos here; those come from the (opt-in) ``TaskPlugin``.
"""

from __future__ import annotations

import sqlite3

from agent_sdk import tool
from agent_sdk.plugins.base import AgentSetup

_MAX_ROWS = 50


class SqliteStore:
    """The connection + query log the bench reads to score (errors, count)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.queries: list[dict] = []


class SqlitePlugin:
    name = "sqlite"

    def __init__(self, store: SqliteStore) -> None:
        self.store = store

    def install(self, setup: AgentSetup) -> None:
        store = self.store

        @tool(name="db.schema")
        async def schema() -> str:
            """Return the database schema (tables, columns, how to join + compute revenue)."""
            from seed import schema_text
            return schema_text()

        @tool(name="db.query")
        async def query(sql: str) -> str:
            """Run a read-only SQL SELECT and return the rows. Compute every fact with this."""
            s = sql.strip().rstrip(";")
            if not s.lower().startswith(("select", "with")):
                return "Error: only read-only SELECT/WITH queries are allowed."
            try:
                cur = store.conn.execute(s)
                cols = [d[0] for d in cur.description or []]
                rows = cur.fetchmany(_MAX_ROWS + 1)
            except sqlite3.Error as exc:
                store.queries.append({"sql": s, "error": str(exc)})
                return f"Error: {exc}. Check db.schema for exact table/column names."
            store.queries.append({"sql": s, "n": len(rows[:_MAX_ROWS])})
            more = "" if len(rows) <= _MAX_ROWS else f"\n… (>{_MAX_ROWS} rows; add LIMIT or aggregate)"
            return (" | ".join(cols) + "\n"
                    + "\n".join(" | ".join(str(c) for c in r) for r in rows[:_MAX_ROWS]) + more)

        setup.add_tool(schema)
        setup.add_tool(query)
