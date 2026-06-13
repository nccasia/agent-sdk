"""Back-compat shim — the skill-activation runtime moved into ``agent_sdk.skills``.

Prefer ``from agent_sdk.skills import SkillToolRuntime, ACTIVATE, READ, SEARCH``.
This re-export keeps the historical ``from agent_sdk.skill_runtime import …`` path
working.
"""

from __future__ import annotations

from agent_sdk.skills.runtime import ACTIVATE, READ, SEARCH, SkillToolRuntime

__all__ = ["SkillToolRuntime", "ACTIVATE", "READ", "SEARCH"]
