from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SECRET_PATTERNS = [
    re.compile(
        r"(?i)\b(authorization|bearer|api[_-]?key|token|password|secret)\b\s*[:=]\s*[^\s,;]+"
    ),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{12,}"),
    re.compile(r"\b[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
]
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"\b(?:\+?\d[\d .-]{7,}\d)\b")


class ChatTurnPayload(BaseModel):
    """Normalized arq chat-turn payload.

    The arq wire shape remains a dict. This model only validates the minimum
    required routing fields and normalizes optional fields used by the worker.
    IDs are kept as strings because tests, platform-bot DMs, and guest refs can
    legitimately pass non-UUID scoped identifiers.
    """

    model_config = ConfigDict(extra="allow")

    trace_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    query: str = ""
    workspace_id: str = ""
    user_id: str = ""
    user_ref: str | None = None
    group_ids: list[str] = Field(default_factory=list)
    conversation_id: str = ""
    bot_version_id: str = ""
    principals: dict[str, Any] = Field(default_factory=dict)
    channel_id: str = ""
    clan_id: str | None = None
    deployment_id: str = "default"
    idempotency_key: str | None = None

    @field_validator("query", mode="before")
    @classmethod
    def _query_to_str(cls, value: Any) -> str:
        return "" if value is None else str(value)

    @field_validator(
        "trace_id",
        "tenant_id",
        "workspace_id",
        "user_id",
        "conversation_id",
        "bot_version_id",
        "channel_id",
        "deployment_id",
        mode="before",
    )
    @classmethod
    def _stringify(cls, value: Any) -> str:
        return "" if value is None else str(value)


def redact_text(value: str, *, max_chars: int = 500) -> str:
    text = value or ""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: f"{m.group(1) if m.lastindex else 'secret'}=[REDACTED]", text)
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    if len(text) > max_chars:
        return text[: max_chars - 1] + "..."
    return text


def sanitize_for_event(value: Any, *, max_chars: int = 500, depth: int = 0) -> Any:
    if depth > 4:
        return "[TRUNCATED]"
    if isinstance(value, str):
        return redact_text(value, max_chars=max_chars)
    if isinstance(value, bytes):
        return redact_text(value.decode("utf-8", errors="replace"), max_chars=max_chars)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_s = str(key)
            if re.search(r"(?i)(authorization|api[_-]?key|token|password|secret)", key_s):
                out[key_s] = "[REDACTED]"
            else:
                out[key_s] = sanitize_for_event(item, max_chars=max_chars, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [
            sanitize_for_event(item, max_chars=max_chars, depth=depth + 1) for item in value[:20]
        ]
    return value
