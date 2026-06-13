"""B3 Tools — adaptive tool-exposure selection (RFC 0015)."""

from agent_sdk.tools.lobes import tool_select

# The lobes this domain owns. ``network.py`` aggregates the core network from each
# domain's ``LOBES``.
LOBES = [tool_select.LOBE]

__all__ = ["tool_select", "LOBES"]
