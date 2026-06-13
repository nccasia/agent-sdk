"""respond — B5 response lobe: render the next reply as a continuation.

The reply-flow lobe. A turn is *collectors → a response stage*: earlier stages
gather (their output carries forward as compact notes); the **terminal** stage is
the response stage, and this lobe frames it to write the NEXT message of the
conversation — continuing the dialogue, never restarting or re-greeting, composed
from the information gathered this turn.

It pairs two ways (full customization, see `docs/concepts/reply-flow.md`):

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
    "You are the assistant continuing this conversation. Using the information gathered this "
    "turn (the notes above) and the conversation so far, write the next reply to the user's "
    "latest message. Continue naturally — do not restart, re-introduce yourself, or re-greet. "
    "Be concrete and direct."
)


class RespondLobe(Lobe):
    """Frame the terminal stage as the response stage — render the next reply,
    continuing the conversation from the gathered notes."""

    id = "respond"
    name = "Respond"
    description = "Render the next reply, continuing the conversation from the gathered notes."
    use_when = "the terminal response stage — composing the reply to the user's latest message"
    how = "a single pass that frames the reply as a continuation using the turn's notes + transcript"
    system_prompt = SYSTEM_PROMPT
    behavior = "compose"
    layer = LAYER_EXPRESSION
    pinned = True
    order = 3

    def activation(self, ctx: dict) -> float:
        return 1.0

    def prompt(self, ctx: TurnContext) -> list[PromptContribution]:
        """Two chunks → two master-prompt sections: the conversation dialog (when this lobe
        runs as a real stage and a transcript is available) then the continuation framing that
        refers to it. ``ctx.session_memory`` is the live ``SessionState``."""
        out: list[PromptContribution] = []
        state = getattr(ctx, "session_memory", None)
        render = getattr(state, "transcript", None)
        if callable(render):
            dialog = render()
            if dialog:
                out.append(PromptContribution(
                    "The conversation so far:\n" + dialog,
                    stability="volatile", source="conversation",
                ))
        out.append(PromptContribution(SYSTEM_PROMPT, stability="stable", source=self.id))
        return out


LOBE = RespondLobe()
SPEC = LOBE.spec  # back-compat export
