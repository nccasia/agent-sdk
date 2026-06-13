"""B4 Cognition — deliberate behavior: the work."""

from agent_sdk.cognition.lobes import (
    classify,
    condense,
    plan,
    research,
    scope_check,
    synthesize,
)

# The lobes this domain owns, in intra-layer execution order (the cognition
# reasoning spine). ``network.py`` aggregates the core network from each domain's
# ``LOBES`` — a new cognition lobe is one entry here, not an edit to a central list.
LOBES = [
    condense.LOBE,
    scope_check.LOBE,
    classify.LOBE,
    plan.LOBE,
    research.LOBE,
    synthesize.LOBE,
]

__all__ = ["classify", "condense", "plan", "research", "scope_check", "synthesize", "LOBES"]
