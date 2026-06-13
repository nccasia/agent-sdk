"""Tools owned by the support-triage plugin."""

from __future__ import annotations

from agent_sdk.tools import tool

__all__ = ["lookup_ticket"]


@tool
def lookup_ticket(ticket_id: str) -> str:
    """Look up the current status of a support ticket by its id."""
    return f"Ticket {ticket_id}: status=open, priority=high, owner=on-call."
