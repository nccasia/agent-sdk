"""Enriched conversation context — the engine's living working memory of a turn's
conversation, distilled and persisted across turns so context management has more
to reason with than the raw transcript.

`react-context-management.md`: context is attention allocation, valued by
CDS = relevance × utility / cost. The richest, highest-density context is not the
history — it is a small curated distillation of *where the conversation is*. This
profile maintains that distillation:

* ``intent``            — qna / task / heavy_doc / channel / config (drives path, tools, budgets)
* ``entities``          — the salient topics in play (sharpens relevance for nodes AND tools)
* ``artifacts``         — manifest of offloaded files/docs available to read (the workspace map)
* ``obligations``       — open sub-questions / todos not yet answered (nothing silently dropped)
* ``facts``             — established constraints/answers (the value-aware KEEP anchors)
* ``recent_tools``      — tools used lately (a utility prior — used last turn ⇒ likely relevant)

It exposes three views over that one state:

* :meth:`signals`      — deterministic flags for the lobe network's signal ctx (never an LLM
                         judging the pipeline — these are free signals).
* :meth:`render`       — a compact, high-density context node the model reads.
* :meth:`keep_anchors` — the ids/keys to PIN full (facts + artifact map) so the funnel never
                         demotes them.

Pure and deterministic. Intent/tool-family recognition is keyword-first (cheap);
an LLM touch can refine it upstream, but the profile itself never calls a model.
Flags reflect the CURRENT turn; persistent fields (facts, artifacts, obligations)
accumulate and the caller decays/clears them as the conversation drifts.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_STOP = {
    "the",
    "a",
    "an",
    "of",
    "to",
    "in",
    "on",
    "for",
    "and",
    "or",
    "is",
    "are",
    "what",
    "who",
    "how",
    "when",
    "where",
    "why",
    "can",
    "you",
    "we",
    "i",
    "me",
    "my",
    "do",
    "does",
    "this",
    "that",
    "it",
    "please",
    "help",
    "mình",
    "cho",
    "là",
    "gì",
    "có",
    "không",
    "và",
    "các",
    "một",
    "này",
    "bạn",
    "giúp",
}

# Intent → the tool FAMILIES that intent needs. Keyword cues recognize the intent
# from the user's own words (the vocabulary the model uses), not the tool names —
# closing the lexical gap that makes name-matching drop fetch_messages/tasks.list.
_INTENT_CUES: dict[str, tuple[set[str], list[str]]] = {
    # intent: (cue tokens, tool families it needs)
    "channel": (
        {
            "said",
            "say",
            "message",
            "messages",
            "chat",
            "channel",
            "discussed",
            "conversation",
            "nói",
            "tin",
            "nhắn",
            "kênh",
            "thread",
        },
        ["channel"],
    ),
    "task": (
        {
            "remind",
            "reminder",
            "schedule",
            "task",
            "todo",
            "tasks",
            "cron",
            "daily",
            "nhắc",
            "lịch",
            "hẹn",
            "nhiệm",
        },
        ["tasks"],
    ),
    "heavy_doc": (
        {
            "convert",
            "document",
            "markdown",
            "html",
            "rewrite",
            "summarize",
            "paste",
            "pasted",
            "doc",
            "file",
            "tài",
            "liệu",
            "chuyển",
        },
        ["workspace"],
    ),
    "config": (
        {
            "connect",
            "configure",
            "setup",
            "settings",
            "onboarding",
            "admin",
            "cấu",
            "hình",
            "kết",
            "nối",
            "cài",
        },
        ["admin"],
    ),
}
_FAMILY_TOOLS = {
    "channel": [],  # fetch/search tools removed — dialog injected via respond lobe
    "tasks": ["tasks.list", "tasks.create", "todos.update"],
    "workspace": ["Glob", "Grep", "Read", "Write"],
    "admin": ["admin.overview", "admin.connect_mello"],
    "kb": ["retrieve_kb", "semantic_search", "keyword_search"],
}


def _tokens(text: str) -> set[str]:
    toks = _WORD.findall(unicodedata.normalize("NFC", (text or "").lower()))
    return {t for t in toks if len(t) >= 3 and t not in _STOP}


@dataclass
class ConversationProfile:
    intent: str = "qna"
    entities: set[str] = field(default_factory=set)
    artifacts: dict[str, int] = field(default_factory=dict)  # path -> size
    obligations: list[str] = field(default_factory=list)  # open sub-questions/todos
    facts: dict[str, str] = field(default_factory=dict)  # established key -> value
    recent_tools: list[str] = field(default_factory=list)
    needs_tools: list[str] = field(default_factory=list)  # tool families for THIS turn

    def update(
        self,
        *,
        query: str | None = None,
        tools_used: list[str] | None = None,
        facts: dict[str, str] | None = None,
        artifacts: dict[str, int] | None = None,
        add_obligations: list[str] | None = None,
        resolved_obligations: list[str] | None = None,
        max_entities: int = 40,
        max_recent_tools: int = 12,
    ) -> ConversationProfile:
        """Fold one turn's signals into the living state. Deterministic."""
        if query is not None:
            qt = _tokens(query)
            self.entities |= qt
            if len(self.entities) > max_entities:  # keep it bounded (decay oldest by drop)
                self.entities = set(list(self.entities)[-max_entities:])
            self.intent, self.needs_tools = self._recognize(qt)
        if tools_used:
            self.recent_tools = (self.recent_tools + list(tools_used))[-max_recent_tools:]
        if facts:
            self.facts.update(facts)
        if artifacts:
            self.artifacts.update(artifacts)
        if add_obligations:
            self.obligations.extend(o for o in add_obligations if o not in self.obligations)
        if resolved_obligations:
            self.obligations = [o for o in self.obligations if o not in resolved_obligations]
        return self

    def _recognize(self, qt: set[str]) -> tuple[str, list[str]]:
        """Intent + needed tool families from the query tokens + persistent state.
        Multi-question bundles ⇒ needs_plan via the caller's signal; here we set
        intent/families. Heavy-doc is sticky once an artifact is offloaded."""
        scored = {name: len(qt & cues) for name, (cues, _fam) in _INTENT_CUES.items()}
        best = max(scored, key=lambda k: scored[k]) if scored else "qna"
        intent = best if scored.get(best, 0) > 0 else "qna"
        if self.artifacts:  # a doc is offloaded → heavy-doc work dominates
            intent = "heavy_doc"
        fams: list[str] = []
        for name, (cues, fam) in _INTENT_CUES.items():
            if qt & cues:
                fams += fam
        if self.artifacts and "workspace" not in fams:
            fams.append("workspace")
        fams.append("kb")  # KB stays a default family (assistant grounding)
        return intent, list(dict.fromkeys(fams))

    # ── three views over the one state ────────────────────────────────────────
    def signals(self) -> dict[str, float]:
        """Free deterministic flags for the lobe-network signal ctx."""
        fams = set(self.needs_tools)
        return {
            "intent_qna": 1.0 if self.intent == "qna" else 0.0,
            "intent_task": 1.0 if self.intent == "task" else 0.0,
            "intent_channel": 1.0 if self.intent == "channel" else 0.0,
            "intent_heavy_doc": 1.0 if self.intent == "heavy_doc" else 0.0,
            "intent_config": 1.0 if self.intent == "config" else 0.0,
            "needs_kb": 1.0 if "kb" in fams else 0.0,
            "needs_channel_tools": 1.0 if "channel" in fams else 0.0,
            "needs_tasks_tools": 1.0 if "tasks" in fams else 0.0,
            "needs_offload": 1.0 if self.artifacts else 0.0,
            "has_obligations": 1.0 if self.obligations else 0.0,
            "has_anchors": 1.0 if self.facts else 0.0,
        }

    def keep_tools(self) -> set[str]:
        """The concrete tools to KEEP exposed this turn (intent-driven, not lexical):
        every tool in a needed family + recently-used tools (utility prior)."""
        keep: set[str] = set(self.recent_tools)
        for fam in self.needs_tools:
            keep |= set(_FAMILY_TOOLS.get(fam, []))
        return keep

    def keep_anchors(self) -> set[str]:
        """Ids/keys to PIN full so the funnel never demotes them: established
        facts (constraints/answers) + the offloaded-artifact map."""
        return set(self.facts) | set(self.artifacts)

    def render(self) -> str:
        """A compact, high-density context node the model reads — 'where we are'."""
        lines = ["## Conversation state", f"intent: {self.intent}"]
        if self.entities:
            lines.append("about: " + ", ".join(sorted(self.entities)[:8]))
        if self.facts:
            lines.append("established:")
            lines += [f"  - {k}: {v}" for k, v in list(self.facts.items())[:8]]
        if self.obligations:
            lines.append("open (not yet answered):")
            lines += [f"  - {o}" for o in self.obligations[:8]]
        if self.artifacts:
            lines.append(f"offloaded files: {len(self.artifacts)} (read via the workspace tools)")
        return "\n".join(lines)
