"""Conversation-scope helpers shared by the path recognizers.

`resolve_prev_path` is the seam that lets a mid-conversation turn activate on
what the chat is ABOUT, not just its bare wording — the previous user turn's
resolved path rides along as the `prev_path` B1 signal.
"""

from __future__ import annotations

# Paths whose previous-turn presence marks an ongoing TASK conversation.
TASKISH_PATHS = frozenset({"task_execute"})
# Paths whose previous-turn presence marks an ongoing INFORMATION thread.
INFOISH_PATHS = frozenset({"qna", "research", "clarify"})


def resolve_prev_path(prev_query: str, *, prev_ctx: dict | None = None, paths=None) -> str | None:
    """The PREVIOUS user turn's resolved path — the conversation-scope signal
    that lets a mid-conversation turn activate on what the chat is ABOUT
    ("thôi bỏ cái đó đi" after a schedule turn is manage; the same words cold
    are nothing). One level deep only: the previous turn is recognized
    without ITS previous turn, so the computation stays pure and bounded.
    Deterministic ⇒ recomputing here equals what turn N-1 resolved live."""
    if not prev_query:
        return None
    from agent_sdk.network.activation import recognize_paths, resolve_path

    if paths is None:
        from agent_sdk.lobes.network import default_paths

        paths = default_paths()
    ctx = {"query": prev_query, "has_history": True}
    ctx.update(prev_ctx or {})
    ctx.pop("prev_path", None)  # bound the recursion
    return resolve_path(recognize_paths(ctx, paths), paths)["name"]
