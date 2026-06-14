"""Default flows — the named paths' pipelines, built from canonical reasoning STATES.

The flow names stay aligned with the path recognizers (``production_flows`` matches a flow to its
path by name to inherit ``grounds``/``threshold``/recognizer), but every flow is now composed from
the canonical, reusable **states** rather than ad-hoc per-intent stages. The names group by
complexity tier (docs/concepts/15-standard-flow.md):

- **direct tier** — ``relational`` = ``[respond]`` (one cheap social reply, no tools).
- **standard tier** — ``qna`` = ``[act]`` · ``clarify`` = ``[understand, act]`` (condense resolves
  the referent, then the workhorse). One agentic ``act`` loop over the full toolset.
- **deep tier** — ``research`` = ``[act, cite, filter]`` — gather, then ground + ground-or-refuse.
- ``fallback`` = ``[act]`` (the universal emergent answer). (Steward/self-config — ``onboarding`` —
  is a host capability contributed by agent-core's Admin plugin, not a generic default flow.)

``act`` is the canonical workhorse state (one agentic ReAct loop); ``cite``/``filter`` are the pinned
grounding states; ``respond`` is the cheap terminal reply. The dynamic state machine (Layer 1,
metacognition) may reshape a flow per turn — e.g. expand ``act`` into ``act → act → act`` over a
plan's subjects — but these seed shapes are the static default. Per-flow customization via
``flow_lobe_weights`` (``flow_disable_<flow>``, ``flow_<flow>__step_<step>__disable``, ``…__lobe…``).
"""

from __future__ import annotations

from agent_sdk.flows.flow import Flow
from agent_sdk.flows.stages import (
    act,
    clarify_synthesize,
    relational_synthesize,
    research_cite,
    research_filter,
)

__all__ = ["default_flows"]


def default_flows() -> list[Flow]:
    """The named paths' default flows, composed from the canonical states."""
    return [
        # standard tier — qna: one agentic act loop over the full toolset.
        Flow(
            name="qna",
            description="qna (standard) — one agentic act loop over the full toolset",
            steps=(act(),),
        ),
        # deep tier — research: act gathers, then cite-ground + ground-or-refuse filter.
        Flow(
            name="research",
            description="research (deep) — act, then ground (cite) + filter",
            steps=(act(), research_cite(), research_filter()),
        ),
        # standard tier — clarify: re-synthesis after the referent is resolved (condense lobe).
        Flow(
            name="clarify",
            description="clarify (standard) — re-synthesis in the resolve-referent phase",
            steps=(clarify_synthesize(),),
        ),
        # direct tier — relational: minimal social reply (no tools, no grounding).
        Flow(
            name="relational",
            description="relational (direct) — one cheap reply for the social register",
            steps=(relational_synthesize(),),
        ),
        # fallback — universal agentic answer for an unrecognized (emergent) turn (≡ qna).
        Flow(
            name="fallback",
            description="fallback — universal agentic act when no named path matches",
            steps=(act(),),
            promotable=False,
        ),
        # NOTE: steward/self-config (``onboarding``) is NOT a generic flow — it's a
        # host/platform capability (admin.* tools) contributed by agent-core's Admin
        # plugin (add_flow + add_stage), not shipped in the SDK default network.
    ]
