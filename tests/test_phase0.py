"""Phase-0 enablers: the selection-hint API + the dynamic context pipeline."""

from __future__ import annotations

from agent_sdk.engine_context import collect_nodes, select_and_render
from agent_sdk.network.context_builder import ContextNode
from agent_sdk.selection import Selected, select_with_hints


# ── selection-hint API ────────────────────────────────────────────────────────
def _items():
    return [
        {"name": "search", "desc": "search the knowledge base for facts"},
        {"name": "deploy", "desc": "ship a release to production"},
        {"name": "weather", "desc": "current weather forecast"},
    ]


def test_select_all_inject_by_default():
    sel = select_with_hints(_items(), "anything", key=lambda i: i["name"], text=lambda i: i["desc"])
    assert all(isinstance(s, Selected) for s in sel)
    assert {s.tier for s in sel} == {"inject"}  # no budget/threshold ⇒ all inject


def test_select_essentials_always_inject():
    sel = select_with_hints(
        _items(), "weather today", key=lambda i: i["name"], text=lambda i: i["desc"],
        inject_threshold=0.9, hint_threshold=0.1, essentials=["deploy"],
    )
    by = {s.key: s.tier for s in sel}
    assert by["deploy"] == "inject"  # essential, despite low relevance
    assert by["weather"] in ("inject", "hint")  # query-relevant


def test_select_budget_demotes_to_hint():
    items = [{"name": f"t{i}", "desc": "x " * 50} for i in range(5)]  # ~25 tok each
    sel = select_with_hints(
        items, "x", key=lambda i: i["name"], text=lambda i: i["desc"],
        budget_tokens=30, min_keep=1,  # fits one, rest demote to hint
    )
    tiers = [s.tier for s in sel]
    assert "inject" in tiers and "hint" in tiers  # budget forces tiering


def test_select_preserves_item_order():
    sel = select_with_hints(_items(), "q", key=lambda i: i["name"], text=lambda i: i["desc"])
    assert [s.key for s in sel] == ["search", "deploy", "weather"]


# ── context pipeline ──────────────────────────────────────────────────────────
class _NodeLobe:
    """A fake lobe that emits two context nodes from build_context."""

    id = "facts"

    def build_context(self, _ctx):
        return [
            ContextNode(id="f1", kind="memory", text="alpha beta gamma", scope=None),
            ContextNode(id="f2", kind="memory", text="zeta eta theta", scope=None),
        ]


def test_collect_nodes_tags_producer():
    lobe = _NodeLobe()
    nodes = collect_nodes(("facts",), {"facts": lobe}, turn_ctx=object())
    assert [lid for lid, _ in nodes] == ["facts", "facts"]
    assert all(isinstance(n, ContextNode) for _, n in nodes)


def test_select_and_render_emits_parts_with_provenance():
    lobe = _NodeLobe()
    nodes = collect_nodes(("facts",), {"facts": lobe}, turn_ctx=object())
    parts = select_and_render(nodes, "alpha beta", budget_tokens=1000)
    # parts are (source, text, stability); the query-relevant node is rendered
    sources = {p[0] for p in parts}
    assert "facts" in sources
    text = "\n".join(p[1] for p in parts)
    assert "alpha" in text


def test_select_and_render_empty_is_noop():
    assert select_and_render([], "q") == []
