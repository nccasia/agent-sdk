"""relational — greeting/social register, no information need.

Score: greeting → 0.9 if ≤8 words, 0.5 if 9–14 (a long greeting is weaker
evidence).
Excluders (→ 0.0): no greeting at all; interrogative with >6 words (a real
question wearing a greeting).

Members/bias: synthesize only, no bias — the recalls stay quiet on their own
signals.
Tuning keys: none by default (`path_relational__synthesize` exists at 0).
Gates: attentionbench `paths` fixtures (greeting archetypes).
"""

from __future__ import annotations

from agent_sdk.lobes.patterns import GREETING_RE, INTERROGATIVE_RE, _word_count
from agent_sdk.network.activation import PathSpec


def recognize(ctx: dict) -> float:
    query = str(ctx.get("query") or "")
    if not GREETING_RE.search(query):
        return 0.0
    if INTERROGATIVE_RE.search(query) and _word_count(query) > 6:
        return 0.0
    return 0.9 if _word_count(query) <= 8 else 0.5


PATH = PathSpec(
    name="relational",
    recognizer=recognize,
    members=("synthesize",),
    bias={},
    grounds=False,  # social/greeting — no KB retrieval, nothing to cite
)
