"""Anti-hedge answer-retry — a builder for the engine's ``_answer_retry`` seam.

Sometimes a one-shot grounded answer *finds* the relevant material but frames it
as an apology ("Sorry, I couldn't find specifics… only general info"), which reads
as a refusal and drops the citation. When that happens AND the turn has a seeded
evidence channel, the engine retries ONCE with the directive this builder returns
(the engine owns the retry loop; the host owns "what counts as a hedge" + the
directive text). The directive does NOT force a fabricated answer — it says
*answer directly from the relevant context if it exists, else keep the refusal* —
so a genuinely unanswerable turn stays refused.

Defaults are English; pass ``markers`` / ``directive`` for another language. Wire
the result onto the engine via ``PreactAgent``'s ``host``/build seam
(``engine._answer_retry = make_hedge_retry()``).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Sequence

__all__ = ["make_hedge_retry", "DEFAULT_HEDGE_MARKERS", "DEFAULT_HEDGE_DIRECTIVE"]

# Hedge openings that read as a refusal but precede real content (English).
DEFAULT_HEDGE_MARKERS: tuple[str, ...] = (
    "sorry", "i couldn't find", "i could not find", "i couldn't locate",
    "i don't have", "i do not have", "unfortunately", "i was unable to find",
    "no specific", "only general",
)

DEFAULT_HEDGE_DIRECTIVE = (
    "You ALREADY have the relevant source passages in the context above. Answer the "
    "question DIRECTLY from the most relevant provisions in them, citing [chunk_id] after "
    "each point. Do NOT open with 'Sorry' / 'I couldn't find' / 'only general information' "
    "when relevant passages exist — present the closest applicable content as the official "
    "answer. ONLY keep a refusal when there is genuinely NO passage relevant to the question."
)


def make_hedge_retry(
    *,
    markers: Sequence[str] = DEFAULT_HEDGE_MARKERS,
    directive: str = DEFAULT_HEDGE_DIRECTIVE,
) -> Callable[[str], str | None]:
    """Return ``(answer) -> directive | None``: the forced-answer directive when the
    answer opens with a hedge marker (checked over the first 160 chars, NFC + lower),
    else None (no retry)."""

    def _is_hedge(answer: str) -> bool:
        head = unicodedata.normalize("NFC", (answer or "")[:160]).lower()
        return any(m in head for m in markers)

    def hedge_retry(answer: str) -> str | None:
        return directive if _is_hedge(answer) else None

    return hedge_retry
