"""The stage catalog — the menu of self-describing Stage building blocks.

A flow is a *cached* plan: a fixed chain of stages. The catalog is the full set
of stage building blocks a planner (programmatic or LLM) selects from to
assemble a plan for a novel problem — the sibling of the lobe registry.

Each entry answers *what / when / how* on its own (``description`` / ``use_when``
/ ``how``), so a programmatic scorer (a stage's ``activation``) or an LLM
("which stages does this problem need?") can pick + order them. The assembled
plan is then walked stage by stage by the same pipeline runner.
"""

from __future__ import annotations

from agent_sdk.flows.stages.cite import ResearchCite
from agent_sdk.flows.stages.common import Stage
from agent_sdk.flows.stages.filter import ResearchFilter
from agent_sdk.flows.stages.plan import ResearchPlan
from agent_sdk.flows.stages.research import KbResearch
from agent_sdk.flows.stages.synthesize import (
    ClarifySynthesize,
    FallbackSynthesize,
    OnboardingSynthesize,
    QnaSynthesize,
    RelationalSynthesize,
    ResearchSynthesize,
)

# Every stage building block, in registry-row form. Extend by adding a class +
# a row here — never an interpreter branch (the lobe/skill/task doctrine).
_STAGE_CLASSES: tuple[type[Stage], ...] = (
    QnaSynthesize,
    FallbackSynthesize,
    ResearchPlan,
    KbResearch,
    ResearchSynthesize,
    ResearchCite,
    ResearchFilter,
    ClarifySynthesize,
    RelationalSynthesize,
    OnboardingSynthesize,
)


def stage_catalog() -> list[Stage]:
    """All stage building blocks as instances."""
    return [cls() for cls in _STAGE_CLASSES]


def catalog_menu() -> list[dict]:
    """The planner's menu — one self-describing row per stage. This is what a
    programmatic scorer or an LLM reads to SELECT + ORDER stages into a plan
    (mirrors how skills/lobes are picked off their ``use_when``/description)."""
    rows: list[dict] = []
    for st in stage_catalog():
        rows.append(
            {
                "id": st.id,
                "flow": st.flow,
                "type": st.spec.type,  # running model: react | simple | map | none
                "description": st.description,
                "use_when": st.use_when,
                "how": st.how,
                "lobes": list(st.lobes),
                "tools": list(st.tools),
            }
        )
    return rows


def get_stage(flow: str, name: str) -> Stage | None:
    """Look up a building-block stage by (flow, name)."""
    for st in stage_catalog():
        if st.flow == flow and (st.id or st.name) == name:
            return st
    return None
