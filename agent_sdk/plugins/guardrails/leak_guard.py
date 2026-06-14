"""Answer-leak post-check factory — the built-in guardrail the plugin ships.

``PluginGuardrails`` is the seam (pre/post checks that raise to block); this is a
ready post-check built on the deterministic detectors in
``agent_sdk.guards.answer_guard``. Wire it as::

    from agent_sdk.plugins.guardrails import PluginGuardrails, make_answer_leak_check

    guard = make_answer_leak_check(forbidden=["internal-only"], impossible_actions=["delete account"])
    agent = PreactAgent(client=…, plugins=[PluginGuardrails(post=[guard])])

The check reads the result's ``.text`` and raises :class:`GuardrailError` on a
leak (secret-shaped string, bulk PII, a forbidden substring) or a commitment to a
declared-impossible action. Everything is injectable; defaults are English.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from agent_sdk.guards.answer_guard import (
    DEFAULT_COMMITMENT_CUES,
    DEFAULT_NEGATION_CUES,
    answer_leak_violation,
    commitment_violation,
)
from agent_sdk.plugins.guardrails.errors import GuardrailError

__all__ = ["make_answer_leak_check"]

_DEFAULT_MESSAGE = "This reply was blocked by the answer-leak guard."


def make_answer_leak_check(
    *,
    forbidden: Sequence[str] = (),
    bulk_pii_threshold: int = 3,
    impossible_actions: Sequence[str] = (),
    commitment_cues: Sequence[str] = DEFAULT_COMMITMENT_CUES,
    negation_cues: Sequence[str] = DEFAULT_NEGATION_CUES,
    message: str = _DEFAULT_MESSAGE,
) -> Callable[[Any], None]:
    """Build a ``PluginGuardrails`` post-check that raises on an answer leak.

    The returned callable takes the turn's ``AgentResult`` (anything with a
    ``.text`` attribute), scans it, and raises :class:`GuardrailError` with the
    violation tag appended when the answer must not ship.
    """

    def check(result: Any) -> None:
        text = getattr(result, "text", "") or ""
        tag = answer_leak_violation(
            text, forbidden=forbidden, bulk_pii_threshold=bulk_pii_threshold
        )
        if tag is None and impossible_actions:
            tag = commitment_violation(
                text,
                impossible_actions,
                commitment_cues=commitment_cues,
                negation_cues=negation_cues,
            )
        if tag is not None:
            raise GuardrailError(f"{message} [{tag}]")

    return check
