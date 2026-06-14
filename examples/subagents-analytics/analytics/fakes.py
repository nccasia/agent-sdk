"""A scripted (offline, deterministic) model that drives the plan-driven fan-out.

No network: the reasoning is scripted, but the SQL is REAL (it runs against the SQLite fixture).
The model PLANS 3 independent analyses with ``TodoWrite`` (each a designed step with its own
``prompt`` + ``tools``); the supervisor sees no deps and picks **fanout**, so the engine runs one
subagent per todo — each runs its own SQL — and the fan-in step builds an executive summary from
the three results. Control flow is keyed on the stage id (plan / execute / fanin) + message
structure, never on sniffing free text.
"""

from __future__ import annotations

from agent_sdk.clients.fake import scripted

# Independent analyses (no deps) ⇒ the supervisor picks fanout: one subagent per todo.
TODOS = [
    {
        "content": "Find the top 3 products by total revenue",
        "status": "pending",
        "activeForm": "Finding the top products by revenue",
        "prompt": "Run one SQL query to find the top 3 products by total revenue, then report them.",
        "tools": ["sql"],
    },
    {
        "content": "Compute total revenue by region",
        "status": "pending",
        "activeForm": "Computing revenue by region",
        "prompt": "Run one SQL query to total revenue by region, then report it.",
        "tools": ["sql"],
    },
    {
        "content": "Compute the monthly revenue trend across 2024",
        "status": "pending",
        "activeForm": "Computing the monthly revenue trend",
        "prompt": "Run one SQL query for the monthly revenue trend across 2024, then report it.",
        "tools": ["sql"],
    },
]
# Each subagent's SQL, chosen by a keyword in its sub-task. Real queries over the fixture.
_QUERY_BY_KEYWORD = {
    "product": "SELECT product, ROUND(SUM(revenue)) AS revenue FROM sales "
    "GROUP BY product ORDER BY revenue DESC LIMIT 3",
    "region": "SELECT region, ROUND(SUM(revenue)) AS revenue FROM sales "
    "GROUP BY region ORDER BY revenue DESC",
    "monthly": "SELECT substr(order_date,1,7) AS month, ROUND(SUM(revenue)) AS revenue FROM sales "
    "GROUP BY month ORDER BY month",
}


def _sql_results(messages) -> list[str]:
    """Every SQL tool-result text already in the transcript (each starts with 'rows:')."""
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    txt = str(b.get("content") or "")
                    if "rows:" in txt:
                        out.append(txt)
    return out


def _top_row(sql_out: str) -> str:
    rows = [ln for ln in sql_out.splitlines() if "|" in ln]
    return rows[1] if len(rows) >= 2 else "(no rows)"


def _query_for(subtask: str) -> str:
    low = subtask.lower()
    if "region" in low:
        return _QUERY_BY_KEYWORD["region"]
    if "month" in low or "trend" in low:
        return _QUERY_BY_KEYWORD["monthly"]
    return _QUERY_BY_KEYWORD["product"]


def make_fake_client():
    def model(stage_id, system, messages, tools):
        last = str(messages[-1]["content"]) if messages else ""
        results = _sql_results(messages)
        # 1. PLAN — write the 3-step plan, then stop the planning loop.
        if stage_id == "plan":
            if "Todos updated" in last:
                return "Plan ready — 3 independent analyses."
            return {"tools": [{"name": "TodoWrite", "input": {"todos": TODOS}}]}
        # 2. EXECUTE — each subagent runs its own SQL (isolated), then reports its row.
        if stage_id == "execute":
            if not results:
                return {"tools": [{"name": "sql", "input": {"query": _query_for(last)}}]}
            return f"Result: {_top_row(results[-1])}"
        # 3. FANIN — aggregate the three subagents' results (rendered into `system`) into a summary.
        seen: set[str] = set()
        rows = []
        for ln in str(system).splitlines():
            if "Result:" in ln:
                val = ln.split("Result:", 1)[-1].strip()
                if val and val not in seen:
                    seen.add(val)
                    rows.append(f"  • {val}")
        findings = "\n".join(rows)
        return (
            "EXECUTIVE SUMMARY (2024 sales)\n"
            + (findings or "  • (results in the per-subagent timelines)")
            + "\n\nTakeaway: revenue concentrates in the top product and leading region, with a "
            "clear upward monthly trend."
        )

    return scripted(model)
