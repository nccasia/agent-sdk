"""B5 Expression — the reply flow.

Core owns ``respond`` (the response-stage continuation framing — every turn renders a reply).
Channel/language/tone styling is the optional ``format`` lobe, shipped as the toggleable
``FormatPlugin`` extension (``agent_sdk/plugins/format/``).
"""

from agent_sdk.expression.lobes import respond

# The lobes this domain owns. ``network.py`` aggregates the core network from each
# domain's ``LOBES``.
LOBES = [respond.LOBE]

__all__ = ["respond", "LOBES"]
