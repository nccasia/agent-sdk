"""The coding agent's stages — the progressive-execution (OX) axis, one file per stage.

A stage is a bounded unit of work with its own lobe slice, tool allowlist, loop
mode, and hop budget. The flows (intents) in :mod:`coding_agent.flows` sequence
these stages. Open the file named for a stage to read its single declaration;
shared tool allowlists live in :mod:`coding_agent.flows.stages._slices`.
"""

from __future__ import annotations

from coding_agent.flows.stages.answer import STAGE as answer
from coding_agent.flows.stages.document import STAGE as document
from coding_agent.flows.stages.explore import STAGE as explore
from coding_agent.flows.stages.implement import STAGE as implement
from coding_agent.flows.stages.investigate import STAGE as investigate
from coding_agent.flows.stages.plan import STAGE as plan
from coding_agent.flows.stages.summarize import STAGE as summarize
from coding_agent.flows.stages.survey import STAGE as survey
from coding_agent.flows.stages.verify import STAGE as verify

# Read-only stages must not write files — block a `bash` heredoc (`cat > FILE`)
# from smuggling a write past the per-stage tool allowlist, and steer a repeated
# full rewrite of the same file toward an edit. Consumed by the DocWriteGuard the
# CodingPlugin installs.
READONLY_STAGES = ("explore", "survey", "investigate", "answer", "plan")


def coding_stages() -> list:
    """Every stage, in registration order (also the codebase-understanding tail:
    survey → investigate → document)."""
    return [explore, plan, implement, verify, answer, summarize,
            survey, investigate, document]


__all__ = ["coding_stages", "READONLY_STAGES"]
