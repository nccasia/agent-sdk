"""Back-compat shim — ``Skill`` moved into the ``agent_sdk.skills`` package.

Prefer ``from agent_sdk.skills import Skill`` (or ``agent_sdk.Skill``). This
re-export keeps the historical ``from agent_sdk.skill_def import Skill`` working.
"""

from __future__ import annotations

from agent_sdk.skills.definition import Skill

__all__ = ["Skill"]
