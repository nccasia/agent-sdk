"""enrich — the turn's opening recognition stage (PreAct).

`enrich` runs FIRST, before `classify`, because its output is what should gate
everything downstream. It is deterministic (keyword-first intent recognition, no
LLM judging the pipeline) and builds/updates the living
:class:`~agent_sdk.react.conversation.ConversationProfile` — a distillation of
*where the conversation is* (intent · entities · offloaded artifacts · open
obligations · established facts · recent tools).

It is the single upstream producer; the rest are consumers of its three views:

* ``profile.signals()``      → merged into the lobe-network signal ctx (activation)
* ``profile.keep_tools()``   → ``_select_tools`` keeps these intent-driven families
* ``profile.keep_anchors()`` → ``_funnel_retier`` pins these full (facts + map)
* ``profile.render()``       → a compact `conversation_state` node the model reads

Inert by default — gated by the ``context_enrich`` policy flag — so adding the
stage leaves the degenerate network byte-identical (parity holds); flip per bot.
The interpreter owns the wiring (``_enrich_profile`` + the three consumer hooks);
this module is the stage's documented contract + a thin builder.
"""

from __future__ import annotations

from agent_sdk.react.conversation import ConversationProfile


def build(
    profile: ConversationProfile | None,
    *,
    query: str,
    tools_used: list[str] | None = None,
    artifacts: dict[str, int] | None = None,
    facts: dict[str, str] | None = None,
) -> ConversationProfile:
    """Build or update the conversation profile for this turn. Deterministic."""
    prof = profile or ConversationProfile()
    return prof.update(query=query, tools_used=tools_used, artifacts=artifacts, facts=facts)
