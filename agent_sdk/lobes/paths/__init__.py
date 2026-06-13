"""Named reasoning paths — recognition, never dispatch (RFC 0015).

Each module exports `recognize(ctx) -> float` and `PATH: PathSpec`. Paths
BIAS member lobes when recognized; the per-lobe activation formula stays
primary, and unmatched shapes resolve to an emergent path.
"""

from agent_sdk.lobes.paths import (
    clarify,
    common,
    onboarding,
    qna,
    relational,
    research,
)

__all__ = [
    "clarify",
    "common",
    "onboarding",
    "qna",
    "relational",
    "research",
]
