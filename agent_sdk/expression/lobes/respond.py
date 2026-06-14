"""respond — B5 response lobe: render the next reply as a continuation.

The reply-flow lobe. A turn is *collectors → a response stage*: earlier stages
gather (their output carries forward as compact notes); the **terminal** stage is
the response stage, and this lobe frames it to write the NEXT message of the
conversation — continuing the dialogue, never restarting or re-greeting, composed
from the information gathered this turn.

It pairs two ways (full customization, see `docs/concepts/03-reply-flow.md`):

- **flow decides** — a flow can list a real ``respond`` stage
  (`expression/stages/respond.py`) as its terminal step;
- **stage decides** — otherwise the engine pins this lobe's framing onto whatever
  the flow's terminal stage is (no extra LLM call).

Pinned (always renders within its stage); contributes prompt only — no tools,
no retrieval. The ground-or-refuse contract stays with cite/filter.
"""

from __future__ import annotations

from agent_sdk.lobes.runtime import Lobe, PromptContribution, TurnContext
from agent_sdk.network.activation import LAYER_EXPRESSION

# The framing in three named parts, so an override can replace just one (e.g. swap
# STYLE for a bot's voice while keeping the continuation + next-step contract).
# A host overrides the reply renderer by contributing a lobe with ``id="respond"``
# (it replaces this builtin — see docs/concepts/03-reply-flow.md):
#
#     class MyRespond(RespondLobe):
#         STYLE = "Reply warmly, like a friendly mentor…"   # tweak one part
#         NEXT_STEP = "…end with a relevant suggestion…"     # …or another
#     # (override prompt() for full control of the contributions.)
#
CONTINUATION = (
    "Write the next reply to the user's latest message, continuing this conversation. Use the "
    "notes gathered this turn and the conversation so far (in the messages). Continue naturally — "
    "do not restart, re-introduce yourself, or re-greet."
)
STYLE = (
    "Be clear and genuinely helpful: give a complete, useful answer (explain what matters, "
    "don't just list)."
)
NEXT_STEP = (
    "Where it fits, end by pointing to a specific, relevant next step that moves the conversation "
    "forward."
)
SYSTEM_PROMPT = f"{CONTINUATION} {STYLE} {NEXT_STEP}"


class RespondLobe(Lobe):
    """Frame the terminal stage as the response stage — render the next reply,
    continuing the conversation from the gathered notes.

    Override-friendly: subclass and set ``system_prompt`` (or compose from the
    ``CONTINUATION`` / ``STYLE`` / ``NEXT_STEP`` parts), then contribute the subclass
    via a plugin (``setup.add_lobe(MyRespond())``) — a same-id lobe replaces this
    builtin. ``prompt()`` reads ``self.system_prompt`` so the override takes effect
    on both the pinned and the explicit-stage paths."""

    id = "respond"
    name = "Respond"
    description = "Render the next reply, continuing the conversation from the gathered notes."
    use_when = "the terminal response stage — composing the reply to the user's latest message"
    how = (
        "a single pass that frames the reply as a continuation using the turn's notes + transcript"
    )
    # The composed framing parts (subclass-overridable individually or wholesale).
    CONTINUATION = CONTINUATION
    STYLE = STYLE
    NEXT_STEP = NEXT_STEP
    system_prompt = SYSTEM_PROMPT
    behavior = "compose"
    layer = LAYER_EXPRESSION
    pinned = True
    order = 3

    def __init_subclass__(cls, **kwargs) -> None:
        # Ergonomic override: if a subclass tweaks a PART but not the whole prompt,
        # recompose ``system_prompt`` from its (possibly-overridden) parts. A subclass
        # that sets ``system_prompt`` explicitly is respected as-is.
        super().__init_subclass__(**kwargs)
        if "system_prompt" not in cls.__dict__:
            cls.system_prompt = f"{cls.CONTINUATION} {cls.STYLE} {cls.NEXT_STEP}"

    def activation(self, ctx: dict) -> float:
        return 1.0

    def prompt(self, ctx: TurnContext) -> list[PromptContribution]:
        """Two contributions: (1) the continuation / conversation-flow rules, then (2) the
        voice + next-step (the reply's tone + how to close). Splitting them lets an override
        replace just the voice while keeping the continuation contract, and keeps each visible
        in the prompt provenance. The conversation itself lives once — in the ``messages`` array
        the engine sends alongside the system prompt (Claude-Code style) — so this lobe does not
        re-inject the transcript (which would duplicate it and churn the cache prefix);
        ``cite``/``filter`` keep the ground-or-refuse contract. Reads ``self.CONTINUATION`` /
        ``self.STYLE`` / ``self.NEXT_STEP`` so a subclass that tweaks a part is honored."""
        rules = self.CONTINUATION.strip()
        voice = " ".join(p for p in (self.STYLE.strip(), self.NEXT_STEP.strip()) if p)
        out: list[PromptContribution] = []
        if rules:
            out.append(PromptContribution(rules, stability="stable", source=self.id))
        if voice:
            out.append(PromptContribution(voice, stability="stable", source=self.id))
        return out


LOBE = RespondLobe()
SPEC = LOBE.spec  # back-compat export
