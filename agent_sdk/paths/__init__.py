"""Named reasoning paths — recognition, never dispatch (RFC 0015).

Each module exports `recognize(ctx) -> float` and `PATH: PathSpec`. Paths
BIAS member lobes when recognized; the per-lobe activation formula stays
primary, and unmatched shapes resolve to an emergent path.
"""

from agent_sdk.paths import (
    clarify,
    common,
    qna,
    relational,
    research,
)

# The paths this domain owns, in recognition order. ``network.py`` aggregates
# ``default_paths`` from here. ``common`` is shared recognizer helpers, not a path.
# Steward/self-config (``onboarding``) is NOT a generic path — it's a host/platform
# concern (admin.* tools) contributed by agent-core's Admin plugin.
PATHS = [qna.PATH, research.PATH, clarify.PATH, relational.PATH]

__all__ = [
    "clarify",
    "common",
    "qna",
    "relational",
    "research",
    "PATHS",
]
