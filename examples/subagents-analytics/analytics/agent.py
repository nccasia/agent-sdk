"""Build the analytics agent: the `sql` tool + the plan-driven fan-out flow."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_sdk import PreactAgent
from agent_sdk.plugins.planning import PlanningPlugin

from analytics.fixture import SqlToolRuntime


class _NoResearch:
    """Drop the RAG ``research`` flow — this analytics agent has no KB, so a complex query should
    route to the ``plan`` (TodoWrite) flow, not research."""

    name = "no_research"

    def install(self, setup) -> None:
        setup.remove_flow("research")


ANALYST_PROMPT = (
    "You are a data analyst with a `sql` tool over a sales database "
    "(table: sales(region, product, category, units, revenue, order_date)). "
    "For a multi-part analytics question, plan it with the TodoWrite tool (one todo per "
    "independent analysis, each with its own prompt + the sql tool). The independent analyses "
    "fan out to a subagent each; you then combine their results into a short executive summary."
)


def build_analytics_agent(conn: sqlite3.Connection, *, client: Any) -> PreactAgent:
    """An agent that plans multi-part analytics with TodoWrite and fans out one subagent per todo.

    The ``plan`` flow is plan → supervise → execute → fanin: the model plans the analyses (each
    todo carries ``sql`` via ``worker_tools=["sql"]``); the supervisor sees no deps and picks
    fanout; the engine runs one isolated subagent per todo (each runs its own SQL); the fan-in
    step aggregates the results into one summary."""
    return PreactAgent(
        client=client,
        instructions=ANALYST_PROMPT,
        tools=[SqlToolRuntime(conn)],
        plugins=[PlanningPlugin(worker_tools=["sql"]), _NoResearch()],
    )
