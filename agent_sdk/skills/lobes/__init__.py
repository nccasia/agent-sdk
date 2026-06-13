"""B3 Skill lobes — learned procedure selection (RFC 0013 skills).

Two lobes by STATE: ``skill_select`` (NON-SELECTED — surface the index, cue
selection) and ``skill_active`` (SELECTED — inject + drive the active skill). They
live inside the ``skills`` package alongside the logic, parser, prompt, and runtime
so everything skill-related is one module.
"""

from agent_sdk.skills.lobes import skill_active, skill_select
from agent_sdk.skills.lobes.skill_active import SkillActiveLobe
from agent_sdk.skills.lobes.skill_select import SkillSelectLobe

# The lobes this domain owns, in intra-layer order (select lists, active drives).
# ``network.py`` aggregates the core network from each domain's ``LOBES``.
LOBES = [skill_select.LOBE, skill_active.LOBE]

__all__ = ["skill_select", "skill_active", "SkillSelectLobe", "SkillActiveLobe", "LOBES"]
