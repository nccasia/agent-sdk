"""The taskbench agent = the opt-in TaskPlugin (how to drive a task) + the bench's
SqlitePlugin (the domain). No flows/stages/todos here — those come from the plugin.

This is the point of the refactor: the bench EXTENDS a task agent with a domain
capability. The agent plans a checklist (TaskPlugin's `plan` stage + `todos` tool),
the engine drives each todo as its own scoped sub-execution (generic `loop="map"`),
and the `deliver` stage states the final answer (graded against reference SQL).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_sdk import PreactAgent
from agent_sdk.plugins import TaskPlugin

from seed import schema_text
from sqlite_plugin import SqlitePlugin, SqliteStore

INSTRUCTIONS = (
    "You answer questions over a real SQLite database by planning a checklist of steps and "
    "executing each with SQL. Compute every fact with db.query (read-only SELECT) — never guess "
    "values or column names. When all steps are done, state the final answer with the concrete "
    "values/names asked for.\n\n" + schema_text()
)


def build_task_agent(client: Any, conn: sqlite3.Connection) -> tuple[PreactAgent, SqliteStore]:
    store = SqliteStore(conn)
    agent = PreactAgent(
        client=client,
        instructions=INSTRUCTIONS,
        plugins=[TaskPlugin(), SqlitePlugin(store)],
        funnel=True,
        tools_in_prompt=True,
        budgets={"stall_patience": 4},
    )
    return agent, store
