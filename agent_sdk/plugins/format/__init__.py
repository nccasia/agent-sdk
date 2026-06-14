"""Format extension — answer styling (channel / language / tone).

A default-on but toggleable extension owning the ``format`` lobe (B5 Expression). Disabling it
(via a ``PluginRegistry``) drops channel/language/tone shaping; the core reply flow (``respond``)
and grounding stay intact. Channel-specific styling (non-English, custom tone, a chat surface's
markdown constraints) is policy-driven (`language`/`tone`/`voice`/`response_format`/
`format_constraints`) — the SDK names no deployment; a platform layer supplies the constraint text.
"""

from __future__ import annotations

from agent_sdk.plugins.base import AgentSetup
from agent_sdk.plugins.format.lobes import format as _format

__all__ = ["FormatPlugin"]


class FormatPlugin:
    name = "format"

    def lobes(self) -> list:
        return [_format.LOBE]

    def install(self, setup: AgentSetup) -> None:
        for lb in self.lobes():
            setup.add_lobe(lb)
