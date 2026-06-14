"""Subagents — named, reusable scoped workers for fan-out (doc 12).

A :class:`Subagent` is the typed, named form of the work-item dict the engine's generic
``loop="map"`` already runs. Declare once (in code via :class:`SubagentRegistry`, or as a
``.claude/agents/*.md`` file via :func:`load_agents_dir`), then delegate by name through the
metacognition ``meta_control(action=fan_out, items=[{agent, input}])`` enactor — Claude Code's
"manual invocation by name", with deterministic resolution.
"""

from __future__ import annotations

from agent_sdk.subagents.definition import Subagent
from agent_sdk.subagents.loader import load_agents_dir, parse_agent_markdown
from agent_sdk.subagents.registry import SubagentRegistry

__all__ = ["Subagent", "SubagentRegistry", "load_agents_dir", "parse_agent_markdown"]
