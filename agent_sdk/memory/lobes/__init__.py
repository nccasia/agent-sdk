"""B2 Memory — recall lobes that enrich the blackboard.

Today's one-pass `_load_*` preloads, transformed into lobes; always-on at
parity defaults (prior 1.0 ≥ threshold 0.5) except `memory_recall`, which is
gated by the policy memory switch.
"""

from agent_sdk.memory.lobes import ctxvar_resolve, memory_recall, session_recall

# The lobes this domain owns, in intra-layer execution order. ``network.py``
# aggregates the core network from each domain's ``LOBES`` — a new memory lobe is
# one entry here (+ its module), not an edit to a central list.
LOBES = [memory_recall.LOBE, session_recall.LOBE, ctxvar_resolve.LOBE]

__all__ = ["ctxvar_resolve", "memory_recall", "session_recall", "LOBES"]
