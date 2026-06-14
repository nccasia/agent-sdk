"""respond — B5 response lobe: render the next reply as a continuation.

The reply-flow lobe. A turn is *collectors → a response stage*: earlier stages
gather (their output carries forward as compact notes); the **terminal** stage is
the response stage, and this lobe frames it to write the NEXT message of the
conversation — continuing the dialogue, never restarting or re-greeting, composed
from the information gathered this turn.

It pairs two ways (full customization, see `docs/concepts/03-reply-flow.md`):

- **flow decides** — a flow can list a real ``respond`` stage
  (`flows/stages/respond.py`) as its terminal step;
- **stage decides** — otherwise the engine pins this lobe's framing onto whatever
  the flow's terminal stage is (no extra LLM call).

Pinned (always renders within its stage); contributes prompt only — no tools,
no retrieval. The ground-or-refuse contract stays with cite/filter.
"""

from __future__ import annotations

from agent_sdk.lobes.runtime import Lobe, PromptContribution, TurnContext
from agent_sdk.network.activation import LAYER_EXPRESSION

SYSTEM_PROMPT = (
    "Write the next reply to the user's latest message, continuing this conversation. Use the "
    "notes gathered this turn and the conversation so far (in the messages). Continue naturally — "
    "do not restart, re-introduce yourself, or re-greet. Be concrete and direct."
)


class RespondLobe(Lobe):
    """Frame the terminal stage as the response stage — render the next reply,
    continuing the conversation from the gathered notes."""

    id = "respond"
    name = "Respond"
    description = "Render the next reply, continuing the conversation from the gathered notes."
    use_when = "the terminal response stage — composing the reply to the user's latest message"
    how = (
        "a single pass that frames the reply as a continuation using the turn's notes + transcript"
    )
    system_prompt = SYSTEM_PROMPT
    behavior = "compose"
    layer = LAYER_EXPRESSION
    pinned = True
    order = 3

    def activation(self, ctx: dict) -> float:
        return 1.0

    def prompt(self, ctx: TurnContext) -> list[PromptContribution]:
        """The continuation framing only. The conversation lives once — in the ``messages``
        array the engine sends alongside the system prompt (Claude-Code style) — so this lobe no
        longer re-injects the transcript into the system prompt (which duplicated it and churned
        the cache prefix). ``cite``/``filter`` keep the ground-or-refuse contract."""
        return [PromptContribution(SYSTEM_PROMPT, stability="stable", source=self.id)]


LOBE = RespondLobe()
SPEC = LOBE.spec  # back-compat export
