"""Conditional capability resolver — per-turn activation matching."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_sdk.plugins.mcp import activation_matches, select_active


@dataclass
class _Item:
    name: str
    activation: dict = field(default_factory=dict)


def test_no_activation_is_always_active():
    items = [_Item("always")]
    assert [i.name for i in select_active(items, {"channel_id": "c1"})] == ["always"]


def test_channel_id_filter():
    items = [
        _Item("a", {"channel_ids": ["c1"]}),
        _Item("b", {"channel_ids": ["c2"]}),
    ]
    assert [i.name for i in select_active(items, {"channel_id": "c1"})] == ["a"]


def test_deployment_id_filter():
    items = [_Item("a", {"deployment_ids": ["d1"]}), _Item("b", {"deployment_ids": ["d2"]})]
    assert [i.name for i in select_active(items, {"deployment_id": "d2"})] == ["b"]


def test_context_flag_default_truthy():
    items = [_Item("a", {"context_flags": ["onboarding"]})]
    assert select_active(items, {}) == []
    assert [i.name for i in select_active(items, {"onboarding": True})] == ["a"]


def test_context_flag_custom_check():
    items = [_Item("dm_only", {"context_flags": ["is_dm"]})]

    def flag_check(flag: str, ctx: dict) -> bool:
        if flag == "is_dm":
            return str(ctx.get("channel_id") or "").startswith("dm:")
        return bool(ctx.get(flag))

    assert select_active(items, {"channel_id": "dm:42"}, flag_check=flag_check)[0].name == "dm_only"
    assert select_active(items, {"channel_id": "chan:7"}, flag_check=flag_check) == []


def test_multiple_conditions_all_must_match():
    item = _Item("x", {"channel_ids": ["c1"], "context_flags": ["onboarding"]})
    assert select_active([item], {"channel_id": "c1", "onboarding": True}) == [item]
    assert select_active([item], {"channel_id": "c1"}) == []  # flag missing
    assert select_active([item], {"onboarding": True}) == []  # channel mismatch


def test_activation_of_extractor_for_dict_items():
    items = [{"name": "a", "activation": {"channel_ids": ["c1"]}}]
    out = select_active(items, {"channel_id": "c1"}, activation_of=lambda d: d.get("activation") or {})
    assert out == items


def test_activation_matches_direct():
    assert activation_matches({"channel_ids": ["c1"]}, {"channel_id": "c1"})
    assert not activation_matches({"channel_ids": ["c1"]}, {"channel_id": "c2"})
