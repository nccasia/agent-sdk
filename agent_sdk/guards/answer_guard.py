"""Answer-side leak guard — the deterministic post-filter twin of the pre-turn gate.

The pre-turn gate inspects the QUERY; this inspects the ANSWER: a final scan that
blocks secret-shaped strings, bulk personal data, and caller-supplied forbidden
phrases from ever leaving the agent — regardless of what the model was talked
into. Prompts own quality; this owns the leak contract.

Pure functions, no LLM, no I/O. The secret/email/phone detectors are
locale-neutral; the commitment / refusal lexicons default to English and are
fully **injectable**, so a host passes its own language's cues without the leaf
carrying that copy. Wrap these with ``make_answer_leak_check`` (in the guardrails
plugin) to get a ``PluginGuardrails`` post-check.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

__all__ = [
    "BULK_PII_THRESHOLD",
    "DEFAULT_COMMITMENT_CUES",
    "DEFAULT_NEGATION_CUES",
    "DEFAULT_REFUSAL_MARKERS",
    "secret_violation",
    "bulk_pii_violation",
    "forbidden_violation",
    "answer_leak_violation",
    "commitment_violation",
    "has_refusal_marker",
]

# Credential-looking value: 20+ url-safe chars containing BOTH letters and
# digits (rules out prose), optionally after a label assignment.
_SECRET_LABEL = re.compile(
    r"(?:api[ _-]?key|secret|token|password|passwd|"
    r"access[ _-]?key|private[ _-]?key)"
    r"[^\n]{0,40}?[:=]?\s*[\"'`]?(?=[A-Za-z0-9_\-]{20,}[\"'`\s.,)]|[A-Za-z0-9_\-]{20,}$)"
    r"(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{20,}",
    re.IGNORECASE,
)
# Vendor-style key prefixes are secrets even without a label.
_SECRET_PREFIX = re.compile(r"\b(?:sk|pk|ghp|xox[bap]|AKIA)[-_][A-Za-z0-9_\-]{12,}")

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Generic phone shapes: international prefix or trunk-0, 9-12 digits,
# tolerant of common separators. Locale-neutral by design.
_PHONE = re.compile(r"(?<!\d)(?:\+\d{1,3}|0)(?:[\s.\-]?\d){8,11}(?!\d)")

# An answer enumerating this many distinct emails/phones is a data dump, not a
# conversational reply.
BULK_PII_THRESHOLD = 3

# Commitment cues looked for BEFORE an impossible-action phrase ("I will <act>").
# English defaults — pass your own language's cues to ``commitment_violation``.
DEFAULT_COMMITMENT_CUES: tuple[str, ...] = (
    "i will",
    "i'll",
    "i have",
    "i've",
    "done",
    "ok",
    "okay",
    "sure",
)
# Negation cues that make a mention SAFE (a refusal talks ABOUT the action).
DEFAULT_NEGATION_CUES: tuple[str, ...] = (
    "cannot",
    "can't",
    "not able",
    "won't",
    "not allowed",
    "unable to",
)
# Phrases that signal an explicit refusal in the answer (English defaults).
DEFAULT_REFUSAL_MARKERS: tuple[str, ...] = (
    "cannot",
    "can't",
    "not able to",
    "unable to",
    "refuse",
    "not allowed",
    "i'm sorry, i can't",
    "i can't help with",
)


def _nfc_lower(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").lower()


def secret_violation(answer: str) -> str | None:
    """``"secret_shaped_string"`` when the answer carries a labelled credential
    or a vendor key prefix, else None."""
    if answer and (_SECRET_LABEL.search(answer) or _SECRET_PREFIX.search(answer)):
        return "secret_shaped_string"
    return None


def bulk_pii_violation(answer: str, *, threshold: int = BULK_PII_THRESHOLD) -> str | None:
    """A violation tag when the answer enumerates ``threshold``+ distinct emails
    or phone numbers (a data dump), else None."""
    if not answer:
        return None
    if len(set(_EMAIL.findall(answer))) >= threshold:
        return "bulk_pii_emails"
    phones = {re.sub(r"\D", "", m) for m in _PHONE.findall(answer)}
    if len(phones) >= threshold:
        return "bulk_pii_phones"
    return None


def forbidden_violation(answer: str, forbidden: Sequence[str]) -> str | None:
    """``"forbidden:<pattern>"`` for the first caller-supplied substring present
    in the answer (NFC, case-insensitive), else None."""
    if not answer:
        return None
    haystack = _nfc_lower(answer)
    for pattern in forbidden or ():
        if _nfc_lower(str(pattern)) in haystack:
            return f"forbidden:{pattern}"
    return None


def answer_leak_violation(
    answer: str,
    *,
    forbidden: Sequence[str] = (),
    bulk_pii_threshold: int = BULK_PII_THRESHOLD,
) -> str | None:
    """Return the first leak-violation tag for an answer, else None.

    Composes the deterministic checks: secret-shaped strings, bulk PII, and
    caller-supplied ``forbidden`` substrings.
    """
    if not answer:
        return None
    return (
        secret_violation(answer)
        or bulk_pii_violation(answer, threshold=bulk_pii_threshold)
        or forbidden_violation(answer, forbidden)
    )


def commitment_violation(
    answer: str,
    actions: Sequence[str],
    *,
    commitment_cues: Sequence[str] = DEFAULT_COMMITMENT_CUES,
    negation_cues: Sequence[str] = DEFAULT_NEGATION_CUES,
) -> str | None:
    """Detect a COMMITMENT to one of the caller-declared impossible ``actions``.

    A bare mention or a negated mention ("I cannot <action>") is safe; a
    commitment cue within the 30 chars before the action phrase, with no negation
    cue in that window, is a violation. No-op when ``actions`` is empty.
    """
    acts = [_nfc_lower(str(a)) for a in (actions or ())]
    if not acts:
        return None
    haystack = _nfc_lower(answer)
    for action in acts:
        start = 0
        while True:
            idx = haystack.find(action, start)
            if idx == -1:
                break
            window = haystack[max(0, idx - 30) : idx]
            if not any(neg in window for neg in negation_cues) and any(
                cue in window for cue in commitment_cues
            ):
                return f"impossible_commitment:{action}"
            start = idx + len(action)
    return None


def has_refusal_marker(answer: str, markers: Sequence[str] = DEFAULT_REFUSAL_MARKERS) -> bool:
    """True when the answer contains an explicit refusal phrase. Verifies that a
    partial-refusal directive was honored — a silent omission or a cheerful
    promise is not a refusal. NFC + casefold matched."""
    haystack = _nfc_lower(answer)
    return any(_nfc_lower(str(m)) in haystack for m in (markers or ()))
