"""respond — the response stage for the OX flow axis.

The terminal *response stage*: after the collector stages gather (their output
carried forward as notes), this stage renders the next message of the
conversation — a continuation, composed from the notes — via the ``respond`` lobe
(`lobes/expression/respond.py`).

Two ways to use it (full customization — see `docs/concepts/reply-flow.md`):

- **flow decides** — list ``respond_step("<flow>")`` as a flow's terminal step in
  ``flows/defaults.py`` to make rendering an explicit pipeline stage;
- **stage decides** — otherwise the engine pins the ``respond`` lobe's framing onto
  whatever the flow's terminal stage is (no extra LLM call).

``.spec`` compiles to the ``FlowStep`` the registry + runner consume, exactly like
its sibling stage modules.
"""

from __future__ import annotations

from agent_sdk.flows.flow import FlowStep
from agent_sdk.flows.stages.common import Stage


class Respond(Stage):
    """Render the next reply, continuing the conversation from the gathered notes."""

    id = "respond"
    description = "respond — render the next reply, continuing the conversation"
    use_when = "the terminal response stage of a conversational flow"
    how = "single call; the respond lobe frames the reply as a continuation using the notes"
    loop = "single"
    lobes = ("respond",)


def respond_step(flow: str) -> FlowStep:
    """The response stage for ``flow`` (its per-flow state surface is flow-qualified)."""
    s = Respond()
    s.flow = flow
    return s.spec
