from typing import Literal

from pydantic import BaseModel


class Citation(BaseModel):
    chunk_id: str
    source_ref: str
    supporting_span: tuple[int, int]  # (start, end)


class Claim(BaseModel):
    text: str
    supporting_chunk_ids: list[str]
    confidence: float  # 0..1


class Memo(BaseModel):
    aspect_id: str
    claims: list[Claim]
    unresolved: list[str]
    tokens_used: int


class Section(BaseModel):
    content: str
    source_memos: list[str]


class Synthesis(BaseModel):
    sections: list[Section]
    open_threads: list[str]


class FinalEnvelope(BaseModel):
    status: Literal["answered", "refused"]
    answer_markdown: str | None
    citations: list[Citation]
    refusal_reason: Literal["no_citations", "budget_exceeded", "policy_violation"] | None
    trace_id: str
    # Specific user-facing refusal text (e.g. a matched refusal rule's `reason`).
    # When set, the worker shows it verbatim instead of the generic category copy.
    refusal_message: str | None = None
    # Successful memory mutations this turn ({action, scope, key}) — the
    # deterministic confirmation footer already rides inside answer_markdown;
    # this field lets surfaces (portal/Mezon) render structured chips.
    memory_updates: list[dict] = []


_FOOTER_PREFIXES = ("• Đã ghi nhớ:", "• đã quên:", "• Memory updated:", "• Forgotten:")


def strip_memory_footer(text: str) -> str:
    """Drop the trailing memory-confirmation line from an answer.

    The footer is chat UI chrome, not conversation content: recorded session
    history must stay clean — a wall of "Đã ghi nhớ" confirmations in history
    teaches the model that saving is already handled and suppresses mid-chain
    saves (contextbench lifecycle finding).
    """
    if not text:
        return text
    lines = text.rstrip().splitlines()
    if lines and lines[-1].strip().startswith(_FOOTER_PREFIXES):
        return "\n".join(lines[:-1]).rstrip()
    return text
