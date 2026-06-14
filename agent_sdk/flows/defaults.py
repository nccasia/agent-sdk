"""Default flows — the 7 named paths' pipelines (Phase 7+).

Each path's default flow preserves the original 2-type QnA quality:

- ``qna`` = ``[synthesize]`` — ONE agentic step over the full
  composed toolset (legacy simple_answer parity). The simple
  graph of the legacy dispatch.
- ``research`` = ``[plan, research, synthesize, cite, filter]`` — the
  3-stage complex graph with KB fanout. The complex graph of the
  legacy dispatch.
- ``task_execute`` = ``[advance, format]`` — agentic todo advance
  (todo-driving tools + memory) then deliver the prepared payload.
- ``clarify`` / ``relational`` = ``[synthesize]`` — one LLM call each.

Per-flow customization (Phase 7e) is via ``flow_lobe_weights``:
- ``flow_disable_<flow>`` — flip a flow off per-bot
- ``flow_<flow>__step_<step>__disable`` — skip a step
- ``flow_<flow>__step_<step>__lobe_<lobe_id>__add`` / ``__remove``
  — mutate the step's lobe slice per-bot
"""

from __future__ import annotations

from agent_sdk.flows.flow import Flow
from agent_sdk.flows.stages import (
    clarify_synthesize,
    fallback_synthesize,
    onboarding_synthesize,
    qna_synthesize,
    relational_synthesize,
    research_cite,
    research_filter,
    research_investigate,
)

__all__ = ["default_flows"]


def default_flows() -> list[Flow]:
    """The 7 named paths' default flows (flow axis registry).

    Each Flow is a complete, named pipeline: an ordered sequence of
    ``FlowStep``s. The interpreter's ``_run_pipeline`` runs them in
    order, each step composing its system prompt from the lobes in
    its slice and running its own agentic loop.

    The 7 named paths' default sequences preserve the original
    2-type QnA quality — qna (simple) is the simple graph; research
    (complex) is the complex graph. The static degenerate network is
    the rollback; adaptive mode layers on top.
    """
    return [
        # qna: one shot, no tools. The simple graph.
        Flow(
            name="qna",
            description="qna answer — one agentic step, full toolset (legacy parity)",
            steps=(qna_synthesize(),),
        ),
        # research: investigate (one ReAct loop over KB tools) → ground + filter.
        # No decompose + map fan-out — the model plans its own sub-steps with TodoWrite.
        Flow(
            name="research",
            description="research — investigate in one ReAct loop, then ground + filter",
            steps=(
                research_investigate(),
                research_cite(),
                research_filter(),
            ),
        ),
        # clarify: single re-synthesis (condense resolves the anaphora)
        Flow(
            name="clarify",
            description="clarify — re-synthesis in the resolve-referent phase",
            steps=(clarify_synthesize(),),
        ),
        # relational: minimal synthesis
        Flow(
            name="relational",
            description="relational — minimal synthesis (greeting / social register)",
            steps=(relational_synthesize(),),
        ),
        # fallback: the standard flow for an UNRECOGNIZED (emergent) turn — a
        # single agentic answer, same contract as qna. Every turn walks a real
        # flow; the flow-less emergent case becomes [synthesize] (RFC 0017).
        Flow(
            name="fallback",
            description="fallback — universal agentic answer when no named path matches",
            steps=(fallback_synthesize(),),
            promotable=False,
        ),
        # onboarding: steward mode — admin.* toolset, no KB recall. Only
        # reachable when the harness flags the conversation (config_mode);
        # the path recognizer is 0.0 otherwise, so normal turns never run it.
        Flow(
            name="onboarding",
            description="onboarding — self-configuration steward mode (admin.* tools)",
            steps=(onboarding_synthesize(),),
        ),
    ]
