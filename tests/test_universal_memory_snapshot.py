"""Universal memory snapshot/restore — the Redis-offloading seam.

A session's durable working memory must round-trip to JSON and back so a stateless worker can
restore it from Redis on the next turn. Flash is turn-scratch and never persists; long-term +
offloaded bodies do.
"""

from __future__ import annotations

from agent_sdk.memory.universal import FLASH_SCOPE, MemoryStore


def _populate() -> MemoryStore:
    s = MemoryStore()
    # flash (turn-scratch — must NOT survive a snapshot)
    s.remember("tool_result", "ephemeral search output", scope=FLASH_SCOPE)
    # long-term durable fact
    s.remember(
        "fact",
        "the deploy window is Thursday 14:00 UTC",
        scope="conversation",
        key="deploy-window",
    )
    # a large body → offloads to DocWorkspace
    big = "# Plan\n\nROOT CAUSE in src/export/csv.py line 142\n\n" + ("detail line\n" * 400)
    s.remember("artifact", big, scope="conversation", key="plan-doc")
    return s


def test_long_term_roundtrips_flash_dropped():
    s = _populate()
    blob = s.to_json()

    restored = MemoryStore.from_json(blob)

    # flash dropped
    assert restored.stats()["flash"] == 0
    # long-term preserved
    assert restored.stats()["long_term"] == s.stats()["long_term"]
    fact = restored.get("mem://fact/conversation/deploy-window")
    assert fact is not None
    assert fact.body == "the deploy window is Thursday 14:00 UTC"
    assert fact.scope == "conversation"


def test_offloaded_body_refetchable_after_restore():
    s = _populate()
    handle = "mem://artifact/conversation/plan-doc"
    assert s.get(handle).offloaded is True

    restored = MemoryStore.from_json(s.to_json())

    e = restored.get(handle)
    assert e is not None and e.offloaded is True
    # the offloaded body is re-fetchable via the DocWorkspace slice path
    matches = restored.grep(handle, "ROOT CAUSE")
    assert matches, "offloaded body must survive snapshot/restore"
    assert "csv.py" in matches[0]["line"]
    # full read still works too
    assert "ROOT CAUSE" in restored.read(handle)


def test_recall_order_and_seq_preserved():
    s = _populate()
    blob = s.to_json()
    restored = MemoryStore.from_json(blob)

    # seq advanced past restored entries so new writes don't collide
    assert restored._seq >= blob["seq"]
    before = restored.stats()["long_term"]
    restored.remember("note", "a brand new note", scope="conversation", key="new")
    assert restored.stats()["long_term"] == before + 1

    found = restored.recall(query="deploy window")
    assert any(e.handle == "mem://fact/conversation/deploy-window" for e in found)


def test_established_fact_survives_snapshot():
    # mirrors auto_establish writing a fact to long-term scope=conversation
    s = MemoryStore()
    s.remember("fact", "user prefers metric units", scope="conversation", key="units")
    restored = MemoryStore.from_json(s.to_json())
    e = restored.get("mem://fact/conversation/units")
    assert e is not None and e.body == "user prefers metric units"


def test_snapshot_bound_keeps_pinned_drops_overflow():
    s = MemoryStore()
    s.remember("decision", "pinned decision", scope="conversation", key="keep", pinned=True)
    for i in range(10):
        s.remember("note", f"note {i}", scope="conversation", key=f"n{i}")

    blob = s.to_json(max_entries=3)
    handles = {e["handle"] for e in blob["long"]}

    assert len(blob["long"]) == 3
    assert "mem://decision/conversation/keep" in handles  # pinned always survives


def test_reset_clears_all():
    s = _populate()
    s.reset()
    assert s.stats() == {
        "flash": 0,
        "long_term": 0,
        "flash_tokens": 0,
        "long_term_tokens": 0,
    }
