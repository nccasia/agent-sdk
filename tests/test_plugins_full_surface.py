"""Plugins are first-class plug-and-play carriers of the full capacity surface.

Locks in the behaviors that ``extensionbench`` declares: a plugin contributes a
lobe/stage/flow/path/skill/tool that becomes resolvable when plugged and not when
unplugged; a plugin can subtract a builtin it owns; pinned cite/filter/synthesize
survive any removal; and no-plugin parity holds.
"""

from __future__ import annotations

import pytest

from agent_sdk import PreactAgent
from agent_sdk.clients.fake import FakeClient
from agent_sdk.plugins import PluginSupportTriage
from agent_sdk.plugins.base import AgentSetup


def _agent(plugins=None) -> PreactAgent:
    return PreactAgent(client=FakeClient(), plugins=plugins or [])


def _caps(agent: PreactAgent) -> dict[str, set[str]]:
    e = agent.engine
    tool_specs = e.tools.get_tool_specs() if e.tools is not None else []
    return {
        "lobe": {lb.id for lb in e.lobes},
        "stage": set(e.stage_registry.ids()),
        "flow": {f.id for f in e.flows},
        "path": {p.name for p in e.path_specs},
        "skill": {p.id for p in e.skill_packs},
        "tool": {str(t.get("name")) for t in tool_specs},
    }


def _path_name(snapshot) -> str:
    p = getattr(snapshot, "path", None)
    if isinstance(p, (tuple, list)) and p:
        return str(p[0])
    return str(p) if p is not None else ""


# (capability kind, the name the example plugin contributes)
SURFACE = [
    ("lobe", "triage"),
    ("stage", "triage"),
    ("flow", "triage"),
    ("path", "triage"),
    ("skill", "triage_policy"),
    ("tool", "lookup_ticket"),
]


@pytest.mark.parametrize("kind,name", SURFACE)
def test_plugged_capability_is_resolvable(kind, name):
    assert name in _caps(_agent([PluginSupportTriage()]))[kind]


@pytest.mark.parametrize("kind,name", SURFACE)
def test_unplugged_capability_is_absent(kind, name):
    assert name not in _caps(_agent())[kind]


def test_disabled_plugin_contributes_nothing():
    p = PluginSupportTriage()
    p.enabled = False
    assert "triage" not in _caps(_agent([p]))["lobe"]


def test_plugin_flow_wins_intent_recognition():
    agent = _agent([PluginSupportTriage()])
    snap = agent.inspect("this incident is urgent, escalate ticket 412, the service is down")
    assert _path_name(snap) == "triage"


def test_path_not_recognized_without_plugin():
    snap = _agent().inspect("this incident is urgent, escalate ticket 412, the service is down")
    assert _path_name(snap) != "triage"


class _Remover:
    name = "remover"

    def __init__(self, *, lobes=(), paths=()):
        self._lobes = list(lobes)
        self._paths = list(paths)

    def install(self, setup: AgentSetup) -> None:
        for lid in self._lobes:
            setup.remove_lobe(lid)
        for name in self._paths:
            setup.remove_path(name)


def test_plugin_can_subtract_builtin_path():
    caps = _caps(_agent([_Remover(paths=["research"])]))
    assert "research" not in caps["path"]
    assert "research" not in caps["flow"]


@pytest.mark.parametrize("pinned", ["filter", "synthesize"])
def test_pinned_lobe_never_removed(pinned):
    # filter (safety, default-on) + synthesize (core) survive a removal attempt.
    caps = _caps(_agent([_Remover(lobes=[pinned])]))
    assert pinned in caps["lobe"]


def test_cite_pinned_within_rag_plugin():
    # cite is opt-in (RagPlugin) — absent by default, present + unremovable with it.
    from agent_sdk.plugins import RagPlugin

    assert "cite" not in _caps(_agent())["lobe"]
    caps = _caps(_agent([RagPlugin(), _Remover(lobes=["cite"])]))
    assert "cite" in caps["lobe"]


def test_no_plugin_parity():
    a, b = _caps(_agent()), _caps(_agent())
    assert a == b


def test_plugged_agent_keeps_all_builtins():
    base = _caps(_agent())
    plugged = _caps(_agent([PluginSupportTriage()]))
    for kind, names in base.items():
        assert names <= plugged[kind], f"{kind}: builtins lost when plugged"
