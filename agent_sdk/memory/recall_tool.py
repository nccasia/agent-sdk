"""The universal read surface + write-to-think, as model-callable tools over a MemoryStore.

Two tools (kept minimal so the catalog stays lean at scale):

* ``recall`` — the read surface: search the digest index (``query``), read a full body
  (``handle`` + ``full``), or slice a large body (``handle`` + ``grep`` / ``section``). This is
  how a digest is expanded back to its detail — the "model can read it back if needed" guarantee.
* ``note`` — write-to-think: jot a ``decision`` / ``note`` / ``sub_goal`` / ``fact`` as a small
  high-utility entry so the reasoning trajectory persists in the palette across hops. A ``fact`` or
  ``decision`` written at ``scope=conversation`` is durable (long-term); the default is flash.

These are ESSENTIALS — adaptive tool selection never drops them (a digest must always be
re-expandable). See ``docs/concepts/universal-memory.md``.
"""

from __future__ import annotations

from typing import Any

from agent_sdk.memory.universal import FLASH_SCOPE, MemoryStore

__all__ = ["RecallToolRuntime"]

_WRITE_KINDS = ("note", "decision", "sub_goal", "hypothesis", "fact", "obligation", "plan")


class RecallToolRuntime:
    """A ``ToolRuntime`` exposing ``recall`` (read) + ``note`` (write-to-think) over a store."""

    def __init__(self, store: MemoryStore):
        self.store = store
        self.writes: list[dict] = []  # structured {kind, scope, handle} this turn

    def get_tool_specs(self) -> list[dict]:
        return [
            {
                "name": "recall",
                "description": (
                    "Read from memory. Use query to search the digest index across everything "
                    "remembered (results, notes, decisions, facts); use handle to expand one entry "
                    "back to its full detail (full=true), or grep/section to read a slice of a large "
                    "body. This is how you read back the detail behind a digest."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "free-text search over digests"},
                        "handle": {"type": "string", "description": "mem://… handle to expand"},
                        "full": {"type": "boolean", "description": "return the full body"},
                        "grep": {"type": "string", "description": "regex; matching lines of a large body"},
                        "section": {"type": "string", "description": "section id; one slice of a large body"},
                        "kind": {"type": "string", "description": "filter the search by kind"},
                    },
                },
            },
            {
                "name": "note",
                "description": (
                    "Write to think: record a decision, note, sub_goal, hypothesis, or established "
                    "fact so it persists in your working memory across steps (you won't have to "
                    "re-derive it). Use scope=conversation for a durable fact/decision; default is "
                    "this turn only."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "kind": {"type": "string", "enum": list(_WRITE_KINDS)},
                        "scope": {"type": "string", "enum": [FLASH_SCOPE, "conversation", "user", "bot"]},
                        "key": {"type": "string"},
                    },
                    "required": ["content"],
                },
            },
        ]

    async def call_tool(
        self,
        name: str,
        inp: dict,
        retrieved_chunks: list[dict] | None = None,
        already_read: set[str] | None = None,
    ) -> str:
        if name == "recall":
            return self._recall(inp)
        if name == "note":
            return self._note(inp)
        return f"Error: unknown tool {name!r}."

    def _recall(self, inp: dict) -> str:
        handle = inp.get("handle")
        if handle:
            if inp.get("section"):
                sec = self.store.read_section(handle, inp["section"])
                return sec if sec is not None else f"(no section {inp['section']!r} in {handle})"
            if inp.get("grep"):
                hits = self.store.grep(handle, inp["grep"])
                return "\n".join(h.get("line", "") for h in hits) or "(no matches)"
            if inp.get("full"):
                body = self.store.read(handle)
                return body if body is not None else f"(unknown handle {handle})"
            entry = self.store.get(handle)
            return entry.digest if entry is not None else f"(unknown handle {handle})"
        results = self.store.recall(query=inp.get("query"), kind=inp.get("kind"))
        if not results:
            return "(nothing remembered yet)"
        return "\n".join(f"- {e.handle} — {e.digest}" for e in results)

    def _note(self, inp: dict) -> str:
        kind = inp.get("kind") if inp.get("kind") in _WRITE_KINDS else "note"
        # `note` means "remember this" — so it is DURABLE by default (survives the turn and the
        # conversation). The model can pass scope=turn for throwaway scratch. (Defaulting to flash
        # silently lost facts the model noted without an explicit scope.)
        scope = inp.get("scope") or "conversation"
        handle = self.store.remember(
            kind, inp.get("content", ""), scope=scope, key=inp.get("key"),
            pinned=(kind in ("decision", "plan", "obligation")), source="note",
        )
        self.writes.append({"kind": kind, "scope": scope, "handle": handle})
        durable = "" if scope == FLASH_SCOPE else " (durable)"
        return f"Noted {kind} as {handle}{durable}."
