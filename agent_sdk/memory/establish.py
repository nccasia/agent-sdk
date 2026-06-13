"""Establish — native, deterministic fact offload.

Relying on the model to call ``note`` for every fact is unreliable (it skips some). ``establish``
makes memorization native: after a turn it scans the user's message for **fact-shaped** statements —
bullet items, and value-bearing sentences (a date, a time, an @owner, a metric, a decision) — and
offloads each to durable memory. So "the user told me X" reliably becomes a recallable fact, no model
cooperation required. Pure and deterministic; deduped by content.
"""

from __future__ import annotations

import hashlib
import re

# A line that is explicitly a fact: a bullet item.
_BULLET = re.compile(r"^\s*[-*•]\s+(.{6,240}?)\s*$", re.M)
# A concrete value that marks a sentence as worth remembering.
_VALUE = re.compile(r"\d{4}-\d{2}-\d{2}|\b\d{1,2}:\d{2}\b|@\w+|\b\d+(?:\.\d+)?\s?(?:ms|%)\b|"
                    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\b", re.I)
# Decision/commitment cues — remember these even without a numeric value.
_CUE = re.compile(r"\b(decided|agreed|owner|deadline|scheduled|rollout|cutover|sla|must|policy|"
                  r"rule|hotline|requirement|trigger|window)\b", re.I)
_SPLIT = re.compile(r"(?<=[.!?])\s+|\n")
# Tokens that are VALUES (what changes when a fact is updated) — stripped to derive the topic, so a
# later version of the same fact CONSOLIDATES over the older one instead of piling up.
_VALUE_TOKENS = re.compile(r"\d{4}-\d{2}-\d{2}|\b\d{1,2}:\d{2}\b|@\w+|\b\d+(?:\.\d+)?\s?(?:ms|%)?\b|"
                           r"\b(?:mon|tue|wed|thu|fri|sat|sun)\w*\b", re.I)

__all__ = ["salient_facts", "fact_key"]


def fact_key(fact: str) -> str:
    """A TOPIC key: the fact with its values stripped, so "nova rollout … Mon 17:00" and "nova rollout
    … Wed 12:00" share a key — the later one overwrites (the fact updated, not duplicated)."""
    topic = re.sub(r"\s+", " ", _VALUE_TOKENS.sub("", fact.lower())).strip(" -:")
    return "est-" + hashlib.sha1((topic or fact.lower()).strip().encode("utf-8")).hexdigest()[:12]


def salient_facts(text: str, *, max_facts: int = 16) -> list[str]:
    """The fact-shaped statements in ``text`` worth remembering — bullets first, then value/cue-bearing
    sentences. Deterministic, deduped, length-bounded."""
    out: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        s = s.strip().strip("-*•").strip()
        k = s.lower()
        if 8 <= len(s) <= 240 and k not in seen:
            seen.add(k)
            out.append(s)

    for m in _BULLET.findall(text or ""):
        add(m)
        if len(out) >= max_facts:
            return out
    for sent in _SPLIT.split(text or ""):
        if _VALUE.search(sent) or _CUE.search(sent):
            add(sent)
            if len(out) >= max_facts:
                break
    return out
