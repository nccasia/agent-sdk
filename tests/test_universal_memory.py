"""Universal memory substrate — entries, two tiers, digest, offload, refetch, promote."""

from __future__ import annotations

from agent_sdk.memory.recall_tool import RecallToolRuntime
from agent_sdk.memory.summarize import compression_ratio, deterministic_digest
from agent_sdk.memory.universal import FLASH_SCOPE, MemoryStore


def test_remember_recall_roundtrip():
    s = MemoryStore()
    h = s.remember("note", "the deploy window is Thursday 14:00 UTC", scope=FLASH_SCOPE)
    assert h.startswith("mem://note/turn/")
    assert s.read(h) == "the deploy window is Thursday 14:00 UTC"
    assert s.get(h).digest  # a gist was produced
    found = s.recall(query="deploy window")
    assert any(e.handle == h for e in found)


def test_digest_preserves_needles():
    body = (
        "ROOT CAUSE: the csv exporter held the full result set in memory.\n"
        "fix in src/export/csv.py line 142\n"
        "thanks everyone, nice work!"
    )
    dg = deterministic_digest("tool_result", {"tool": "read_file"}, body)
    assert "src/export/csv.py" in dg  # path needle kept
    assert "142" in dg  # number needle kept
    assert "ROOT CAUSE" in dg  # decision needle kept


def test_digest_compresses_large_body():
    # The compression payoff is on LARGE bodies — the needles ride near the top, the
    # bulk (chatter/repetition) is dropped by the bounded digest.
    body = (
        "DEADLINE: 2026-07-15 owner @lan in src/plan.py\n"
        + "routine status chatter, nothing decision-relevant here. " * 200
    )
    dg = deterministic_digest("note", {}, body)
    assert "2026-07-15" in dg and "src/plan.py" in dg  # needles survive
    assert compression_ratio(dg, body) < 0.2  # the bulk is dropped


def test_large_body_offloads_and_slices():
    s = MemoryStore(large_body_chars=200)
    body = "# Overview\nsmall intro\n\n# Details\n" + ("detail line with data 42\n" * 60)
    h = s.remember("temp_file", body, scope=FLASH_SCOPE, meta={"path": "report.md"})
    assert s.get(h).offloaded
    assert s.read(h) == body  # full still available
    hits = s.grep(h, "data 42")
    assert hits  # grep returns matching lines, not the whole body
    outline = s.outline(h)
    assert any(sec["heading"] == "Details" for sec in outline)
    section = s.read_section(h, outline[1]["id"])
    assert "detail line" in section


def test_two_tiers_and_promote():
    s = MemoryStore()
    flash = s.remember("fact", "user prefers dark mode", scope=FLASH_SCOPE)
    assert s.get(flash).is_flash
    promoted = s.promote(flash, scope="user", key="ui_pref")
    assert promoted.startswith("mem://fact/user/")
    assert not s.get(promoted).is_flash
    # flash is dropped at turn end; the promoted long-term entry survives
    s.reset_flash()
    assert s.get(flash) is None
    assert s.read(promoted) == "user prefers dark mode"


def test_tiering_pins_and_no_silent_drop():
    s = MemoryStore()
    pin = s.get(s.remember("decision", "ship behind a flag", pinned=True))
    rel = s.get(s.remember("fact", "the zephyr deadline is 2026-07-15"))
    junk = s.get(s.remember("tool_result", "x " * 400, meta={"tool": "noise"}))
    s.tier([pin, rel, junk], query="zephyr deadline", budget_tokens=10_000)
    assert pin.tier == 1  # pinned floors to inject-full
    assert rel.tier == 1  # relevant + small → inject
    assert junk.tier in (2, 3)  # large + off-topic → digest/offload, not full


def test_compaction_summarizer_offloads_and_is_refetchable():
    s = MemoryStore()
    summarize = s.compaction_summarizer()
    raw = "DEPLOY WINDOW: Thursday 14:00 UTC owner @lan ticket OPS-1102 " + ("detail " * 50)
    digest = summarize("retrieve_kb", {"q": "deploy"}, raw)
    assert "read('mem://tool_result/turn/" in digest  # the digest names the handle
    handle = digest.split("read('")[1].split("')")[0]
    assert s.read(handle) == raw  # the spent body is offloaded but re-fetchable


def test_render_index_is_the_discoverable_menu():
    s = MemoryStore()
    s.remember("decision", "ship behind the flag", scope="conversation", key="ship")
    for i in range(20):
        s.remember("tool_result", f"result {i} about deploy", meta={"tool": "fetch"})
    idx = s.render_index(budget_tokens=200, max_per_kind=5)
    assert idx.startswith("## Memory")
    assert "ship behind the flag" in idx  # the decision is listed
    assert "recall(query" in idx  # capped entries are announced, not hidden
    # an entry not shown in the capped index is still findable by search
    hits = s.recall(query="result 2 about deploy")
    assert hits and any("result 2" in s.read(e.handle) for e in hits)


async def test_recall_tool_read_and_write():
    s = MemoryStore()
    tool = RecallToolRuntime(s)
    # write-to-think
    out = await tool.call_tool("note", {"content": "use the funnel approach", "kind": "decision"})
    assert "Noted decision" in out
    assert tool.writes and tool.writes[0]["kind"] == "decision"
    # read it back via search
    found = await tool.call_tool("recall", {"query": "funnel approach"})
    assert "decision" in found
    # read a full body via handle
    h = s.remember("tool_result", "the full body here", meta={"tool": "fetch"})
    body = await tool.call_tool("recall", {"handle": h, "full": True})
    assert body == "the full body here"
