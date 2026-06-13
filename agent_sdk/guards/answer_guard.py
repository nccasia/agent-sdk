"""Answer-side leak guard (assistant-hardening 1.3).

The pre-reasoning refusal gate inspects the QUERY; nothing inspected the
ANSWER. This is the deterministic answer-side twin: a final post-filter scan
that blocks secret-shaped strings, bulk personal data, and per-policy
forbidden phrases from ever leaving the bot — regardless of what the model
was talked into. Prompts own quality; this owns the leak contract.

Pure functions, no LLM, no I/O — called by the interpreter on the final
answer text.
"""

from __future__ import annotations

import re
import unicodedata

# Credential-looking value: 20+ url-safe chars containing BOTH letters and
# digits (rules out prose), optionally after a label assignment.
_SECRET_LABEL = re.compile(
    r"(?:api[ _-]?key|secret|token|password|passwd|mật khẩu|khóa bí mật|"
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

# An answer enumerating this many distinct emails/phones is a data dump, not
# a conversational reply.
_BULK_PII_THRESHOLD = 3

# Language-keyed engine defaults (UX copy, not org content) — a policy's
# ``answer_guard_message`` always wins; the bot's ``language`` picks the
# fallback.
_GUARD_MESSAGES = {
    "vi": (
        "Xin lỗi, mình không thể gửi nội dung này — nó có thể chứa thông tin "
        "nhạy cảm (bí mật hệ thống hoặc dữ liệu cá nhân hàng loạt). Bạn diễn "
        "đạt lại phần cần hỗ trợ giúp mình nhé."
    ),
    "en": (
        "Sorry, I can't send this reply — it may contain sensitive content "
        "(system secrets or bulk personal data). Please rephrase the part "
        "you need help with."
    ),
}
DEFAULT_GUARD_MESSAGE = _GUARD_MESSAGES["vi"]  # back-compat alias (tests)


def _nfc_lower(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").lower()


def answer_guard_violation(answer: str, policy: dict) -> str | None:
    """Return a violation tag when the final answer must not ship, else None.

    Checks (all deterministic):
    - secret-shaped strings (labelled credentials, vendor key prefixes)
    - bulk PII (>= 3 distinct emails or phone numbers in one answer)
    - per-policy ``answer_must_never`` substrings (NFC, case-insensitive)
    """
    if not answer:
        return None

    if _SECRET_LABEL.search(answer) or _SECRET_PREFIX.search(answer):
        return "secret_shaped_string"

    if len(set(_EMAIL.findall(answer))) >= _BULK_PII_THRESHOLD:
        return "bulk_pii_emails"
    phones = {re.sub(r"\D", "", m) for m in _PHONE.findall(answer)}
    if len(phones) >= _BULK_PII_THRESHOLD:
        return "bulk_pii_phones"

    haystack = _nfc_lower(answer)
    for pattern in policy.get("answer_must_never") or []:
        if _nfc_lower(str(pattern)) in haystack:
            return f"answer_must_never:{pattern}"
    return None


def guard_message(policy: dict) -> str:
    configured = policy.get("answer_guard_message")
    if configured:
        return str(configured)
    lang = str(policy.get("language") or "vi")
    return _GUARD_MESSAGES.get(lang, _GUARD_MESSAGES["en"])


# ── capability-manifest honesty (assistant-hardening 2.2) ─────────────────────
#
# The ENGINE ships only the detection mechanics. WHICH actions are impossible
# is pure bot configuration (``policy.impossible_actions``) — a university bot
# lists grade changes, a bank bot lists transfers, an HR bot lists contract
# edits. No org/domain content lives here.

# Commitment cues looked for BEFORE the action phrase ("đã <action>",
# "I will <action>"). Language-level lexicon (vi/en — the platform's supported
# answer languages), not org content.
_COMMITMENT_CUES = (
    "đã",
    "sẽ",
    "để mình",
    "mình sẽ",
    "em sẽ",
    "ok",
    "okay",
    "vâng",
    "i will",
    "i'll",
    "i have",
    "done",
)
# Negation cues that make the mention SAFE (a refusal talks ABOUT the action).
_NEGATION_CUES = (
    "không",
    "ko ",
    "chẳng",
    "chưa thể",
    "cannot",
    "can't",
    "not able",
    "won't",
    "không thể",
    "không có quyền",
    "không được phép",
)


def capability_violation(answer: str, policy: dict) -> str | None:
    """Detect a COMMITMENT to a policy-declared impossible action.

    A bare mention or a negated mention ("mình không thể <action>") is safe;
    a commitment cue within the 30 chars before the action phrase, with no
    negation cue in that window, is a violation. No-op when the policy
    declares no ``impossible_actions``.
    """
    actions = [_nfc_lower(str(p)) for p in (policy.get("impossible_actions") or [])]
    if not actions:
        return None
    haystack = _nfc_lower(answer)
    for action in actions:
        start = 0
        while True:
            idx = haystack.find(action, start)
            if idx == -1:
                break
            window = haystack[max(0, idx - 30) : idx]
            if not any(neg in window for neg in _NEGATION_CUES) and any(
                cue in window for cue in _COMMITMENT_CUES
            ):
                return f"impossible_commitment:{action}"
            start = idx + len(action)
    return None


# ── refusal-marker verification (assistant-hardening 2.3) ─────────────────────

# Phrases that signal an explicit refusal in the answer. Extendable per policy
# via ``refusal_markers``. NFC + casefold matched.
_DEFAULT_REFUSAL_MARKERS = (
    "không thể",
    "không được phép",
    "không có quyền",
    "không cung cấp",
    "không chia sẻ",
    "không tiết lộ",
    "từ chối",
    "xin lỗi, mình không",
    "cannot",
    "can't",
    "not able to",
    "refuse",
    "not allowed",
)


def has_refusal_marker(answer: str, policy: dict) -> bool:
    """True when the answer contains an explicit refusal phrase. Used to
    VERIFY the partial-refusal directive was honored — a silent omission or a
    cheerful promise is not a refusal."""
    haystack = _nfc_lower(answer)
    markers = list(policy.get("refusal_markers") or []) + list(_DEFAULT_REFUSAL_MARKERS)
    return any(_nfc_lower(str(m)) in haystack for m in markers)
