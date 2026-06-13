"""PreAct — context that funnels toward the answer instead of accumulating.

`react-context-management.md`: vanilla ReAct appends every observation, so the
window grows each hop and attention dilutes exactly as the trajectory should be
narrowing. PreAct re-tiers the working set every hop — the newest
observation stays full, spent ones demote to a discoverable hint, the stable
prefix is left byte-identical for the prompt cache.

This is the *inner* loop of a PreAct agentic step (`tool_loop`'s ``retier``
hook); the macro PreAct architecture decides which steps run and what they see.
"""

from agent_sdk.react.funnel import compact_observations, tier_observations

__all__ = ["compact_observations", "tier_observations"]
