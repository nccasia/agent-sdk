"""Shared lexical cue patterns (vi + en) for lobe signals and path recognition.

Stdlib-only; every pattern is NFC-tolerant and case-insensitive. These are the
B1 substrate's free, deterministic feature detectors — a lobe or recognizer
imports exactly the patterns it consumes, so the receptive surface of each
unit is visible in its import list.

Tuning note: patterns are SHARED — widening one (say MUTATION_RE) shifts every
consumer's recognizer scores. The `paths` and `lobe-builder` attentionbench
modes plus the degenerate-parity matrix in tests/test_lobe_network.py gate any
change here.
"""

from __future__ import annotations

import re
import unicodedata


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").lower()


def _word_count(query: str) -> int:
    return len((query or "").split())


# Same heuristic the condense gate uses today (interpreter._ANAPHORA_RE) —
# duplicated here so the pure package never imports the 3k-line interpreter;
# the parity test pins the two patterns together.
ANAPHORA_RE = re.compile(
    r"(\bnày\b|\bđó\b|\bđấy\b|\bkia\b|\bnó\b|\bvậy\b|\bấy\b|\bở trên\b"
    r"|\bthis\b|\bthat\b|\bit\b|\bthese\b|\bthose\b)",
    re.IGNORECASE,
)

TIME_RECURRENCE_RE = re.compile(
    r"(hằng ngày|hàng ngày|hằng tuần|hàng tuần|mỗi (sáng|trưa|chiều|tối|ngày|tuần|tháng)"
    r"|thứ [2-7]\b|thứ (hai|ba|tư|năm|sáu|bảy)|chủ nhật"
    r"|lúc \d{1,2}\s?(h|g|giờ|:)|\d{1,2}\s?(giờ|h)(\d{2})?\b|\d{1,2}:\d{2}"
    r"|trước hạn|deadline"
    r"|every (day|morning|evening|week|month|monday|tuesday|wednesday|thursday|friday"
    r"|saturday|sunday)|daily|weekly|monthly|at \d{1,2}(:\d{2})?\s?(am|pm)?\b|tomorrow|ngày mai)",
    re.IGNORECASE,
)

REMINDER_RE = re.compile(
    r"(nhắc (mình|tôi|tớ|em|anh|chị|nhở)?|đặt lịch|lên lịch|hẹn giờ|tạo (task|nhắc|lịch)"
    r"|\bremind\b|\bschedule\b|set (a |an )?(reminder|alarm|task))",
    re.IGNORECASE,
)

MUTATION_RE = re.compile(
    r"(đổi (sang|lại|giờ|lịch)|đổi [^,.;!?]{0,24}sang|sửa (lại|giờ|lịch)"
    r"|hủy|huỷ|xóa|xoá|tắt (nhắc|lịch|task)"
    r"|dừng (nhắc|lịch|task)|tạm dừng|bật lại|chạy lại"
    r"|\bcancel\b|\breschedule\b|change (it|the (time|schedule|task))"
    r"|\bdelete\b|\bpause\b|\bresume\b|turn off)",
    re.IGNORECASE,
)

TASK_NOUN_RE = re.compile(
    r"(nhắc nhở|lời nhắc|lịch (nhắc|hẹn|chạy)|\btask\b|\breminder\b|việc (đã|đang) (đặt|hẹn))",
    re.IGNORECASE,
)

GREETING_RE = re.compile(
    r"^(xin )?(chào|chao|hello|hi|hey|yo|alo)\b|"
    r"(cảm ơn|cám ơn|thank(s| you)?|tạm biệt|good (morning|night)|bye)\b",
    re.IGNORECASE,
)

# Self-reference: the user asks ABOUT the assistant itself (identity / capability /
# introduction). These are social/relational, not an information need against the
# knowledge base — so they belong on the relational path (answer from persona, no
# KB retrieval, no citations) even though "bạn là ai" is syntactically interrogative.
SELF_REF_RE = re.compile(
    r"\bbạn (là (ai|gì|người (gì|nào))|tên (là )?gì|giúp (được )?(gì|được gì)"
    r"|làm (được )?gì|hỗ trợ (được )?gì|có thể (làm|giúp))"
    r"|giới thiệu (về )?(bạn|bản thân)"
    r"|who are you|what can you (do|help)|introduce yourself",
    re.IGNORECASE,
)

# Soft cancellation/abandonment cues — too weak to mean "mutate a task" on
# their own, but inside a conversation whose PREVIOUS turn was task-shaped
# ("nhắc mình ôn bài mỗi tối 21h" → "thôi bỏ cái đó đi") they are exactly a
# manage turn. Conversation-scope context disambiguates what the bare query
# cannot (RFC 0015: session state legitimately feeds the signals).
SOFT_CANCEL_RE = re.compile(
    r"(\bthôi\b.{0,16}(bỏ|khỏi|dừng|đừng|hủy|huỷ)|khỏi cần|không cần (nữa|đâu)"
    r"|bỏ (cái )?đó đi|dẹp (cái )?đó|never ?mind|forget (it|that)|drop (it|that))",
    re.IGNORECASE,
)

INTERROGATIVE_RE = re.compile(
    r"(là gì|là ai|khi nào|lúc nào|ở đâu|bao nhiêu|bao lâu|mấy giờ|thế nào|làm sao"
    r"|vì sao|tại sao|có (được|phải|đúng)? ?không"
    r"|\bwhat\b|\bwhen\b|\bwhere\b|\bwho\b|\bwhy\b|\bhow\b|\?)",
    re.IGNORECASE,
)

# A DECLARATIVE information request ("tôi cần hướng dẫn …", "hướng dẫn mình cách …",
# "cho mình biết về …", "giải thích …") is a knowledge query just like an
# interrogative — it needs KB grounding — but carries no question marker, so the
# interrogative gate misses it and the turn falls to the no-retrieval emergent
# path and answers UNGROUNDED. Action/task requests ("đặt lịch", "nhắc tôi") are
# excluded upstream by MUTATION_RE/REMINDER_RE, so this only credits requests for
# INFORMATION. Keep it intent-shaped (verbs of asking/guiding), not topical.
INFO_REQUEST_RE = re.compile(
    r"(cần (biết|hiểu|rõ|hướng dẫn|thông tin|tư vấn|hỗ trợ|tìm hiểu|giải thích)"
    r"|hướng dẫn (tôi|mình|em|giúp|cách|về|sử dụng|dùng)"
    r"|chỉ (tôi|mình|em|giúp) (cách|cách dùng|cách sử dụng)"
    r"|cho (tôi|mình|em) (biết|hỏi|xin) (về|thông tin|cách|chi tiết)?"
    r"|giải thích (giúp|cho|về)?|giúp (tôi|mình|em) (hiểu|tìm hiểu|nắm|với)"
    r"|muốn (biết|hiểu|tìm hiểu|hỏi|nắm)"
    r"|tìm hiểu về|thông tin về"
    r"|\bi need (info|information|help|a guide|guidance|to understand)\b"
    r"|\bhelp me (understand|with)\b|\bguide me\b|\bexplain\b|\btell me about\b"
    r"|\bhow (do|can) i (use|do)\b)",
    re.IGNORECASE,
)

COMPARATIVE_RE = re.compile(
    r"(so sánh|đối chiếu|khác (nhau|gì)|giống (nhau|gì)|ưu (và )?nhược|hơn hay"
    r"|\bcompare\b|\bversus\b|\bvs\.?\b|pros and cons|trade-?offs?"
    r"|phân tích|tổng hợp|liệt kê (tất cả|toàn bộ)|\banalyze\b|\bsummarize all\b)",
    re.IGNORECASE,
)

FIRED_PROMPT_RE = re.compile(r"\[Scheduled task execution\]", re.IGNORECASE)

# A recurrence CADENCE (hằng tuần / mỗi sáng / every Monday / thứ Ba) paired
# with an explicit CLOCK time is a scheduling intent even with no reminder
# verb: "tổng hợp danh sách lỗi hằng tuần lúc 9h" schedules a recurring digest,
# it does not ask to summarize NOW. The recurrence+time frames the turn as
# scheduling — so a research-shaped payload under it is a task turn (the same
# "intent dominates shape" rule research already applies to reminder verbs).
_CADENCE_RE = re.compile(
    r"(hằng|hàng|mỗi (sáng|trưa|chiều|tối|ngày|tuần|tháng)"
    r"|thứ [2-7]\b|thứ (hai|ba|tư|năm|sáu|bảy)|chủ nhật"
    r"|every (day|morning|evening|week|month|monday|tuesday|wednesday|thursday|friday"
    r"|saturday|sunday)|daily|weekly|monthly)",
    re.IGNORECASE,
)
_CLOCK_RE = re.compile(
    r"(lúc \d{1,2}\s?(h|g|giờ|:)|\d{1,2}\s?(giờ|h)(\d{2})?\b|\d{1,2}:\d{2}"
    r"|at \d{1,2}(:\d{2})?\s?(am|pm)?\b)",
    re.IGNORECASE,
)


def is_recurring_schedule(query: str) -> bool:
    """True when the query pairs a recurring cadence with an explicit clock
    time — an unambiguous scheduling intent (no reminder verb required)."""
    return bool(_CADENCE_RE.search(query) and _CLOCK_RE.search(query))


# ── Self-configuration mode (conversational onboarding/settings) ────────────
# Deterministic COMMANDS, not intent heuristics: the admin types the exact
# token ("@bot onboarding" arrives mention-stripped as "onboarding") to enter,
# and an exact exit token to leave. Anchored to the whole query so ordinary
# questions that merely mention these words never flip the mode. The worker's
# activation helper (not the LLM) consumes these; the `onboarding` path
# recognizer keys on the resulting `config_mode` ctx flag only.
CONFIG_CMD_RE = re.compile(
    r"^\s*(?:@\S+\s+)?/?(?:onboarding|config|settings?"
    r"|cấu hình(?:\s+bot)?|thiết lập(?:\s+(?:bot|kênh))?|cài đặt(?:\s+bot)?)"
    r"\s*[.!]?\s*$",
    re.IGNORECASE,
)

CONFIG_EXIT_RE = re.compile(
    r"^\s*(?:@\S+\s+)?/?(?:done|exit|quit|xong|thoát|kết thúc|hoàn tất)\s*[.!]?\s*$",
    re.IGNORECASE,
)

# Admin "relearn / update the standard answer" trigger. Enters config mode (so
# the Builder-role gate + sticky phase are reused) and foregrounds the
# `standard_answer_update` skill. Unlike CONFIG_CMD_RE this is NOT anchored to
# the whole message — the admin may append the question to relearn inline
# ("@bot relearn: làm sao đặt lại mật khẩu?"). Capture group 1 = that inline
# question (empty ⇒ recover it from the prior turn).
RELEARN_CMD_RE = re.compile(
    r"^\s*(?:@\S+\s+)?/?"
    r"(?:relearn|update\s+standard\s+answer"
    # "học lại CÂU …" only (relearn the question/answer) — NOT bare "học lại",
    # which is the learner phrase for re-taking a course.
    r"|học\s*lại\s+câu(?:\s*(?:hỏi|trả\s*lời|này|đó))?"
    r"|cập\s*nhật\s*câu\s*trả\s*lời(?:\s*chuẩn)?"
    r"|sửa\s*câu\s*trả\s*lời)"
    r"\s*[:\-–]?\s*(?P<q>.*?)\s*[.!?]?\s*$",
    re.IGNORECASE | re.DOTALL,
)

CONFIG_CONFIRM_RE = re.compile(
    r"^\s*(?:có|yes|ok(?:ay)?|đồng ý|xác nhận|confirm|đúng vậy|chốt)\s*[.!]?\s*$",
    re.IGNORECASE,
)

# Explicit slash-command skill invocation (on-demand activation by slug). This
# is NOT natural-language intent matching — a LEADING SLASH is required, so
# ordinary prose that merely names a skill never matches. It surfaces the named
# skill as a CANDIDATE for the turn; the model still decides and activates it
# via the ActivateSkill tool (no hard activation). Forms accepted:
#   /mello-config   /skill mello_onboarding   /mello_onboarding
SKILL_SLASH_RE = re.compile(
    r"^\s*(?:@\S+\s+)?/(?:skill\s+)?(?P<slug>[a-z0-9][a-z0-9_-]{1,63})\s*$",
    re.IGNORECASE,
)

# Friendly slash aliases → canonical skill slug. Plain slugs (e.g.
# "/mello_onboarding") resolve to themselves and need no entry here.
SKILL_SLASH_ALIASES = {
    "mello-config": "mello_onboarding",
    "mello": "mello_onboarding",
}
