"""PluginRegistry — manage builtin/custom plugins: enable / disable / override.

A registry is the control surface: register by name, toggle, override a builtin with your own,
and pass it straight to ``PreactAgent(plugins=…)`` which installs ``registry.active()``.
"""

from __future__ import annotations

from agent_sdk import PreactAgent
from agent_sdk.clients.fake import FakeClient
from agent_sdk.plugins import PluginRegistry, PluginSupportTriage, builtin_registry


def _lobes(agent) -> set[str]:
    return {lb.id for lb in agent.engine.lobes}


def test_register_enable_disable():
    p = PluginSupportTriage()
    reg = PluginRegistry([p])
    assert reg.active() == [p] and reg.is_enabled("support_triage")
    reg.disable("support_triage")
    assert reg.active() == [] and not reg.is_enabled("support_triage")
    reg.enable("support_triage")
    assert reg.active() == [p]


def test_override_by_name():
    class _Other:
        name = "support_triage"

        def install(self, setup):
            return None

    reg = PluginRegistry([PluginSupportTriage()])
    other = _Other()
    reg.override(other)  # same name → replaces in place
    assert reg.get("support_triage") is other
    assert reg.active() == [other] and reg.names() == ["support_triage"]


def test_preactagent_accepts_a_registry():
    reg = PluginRegistry([PluginSupportTriage()])
    agent = PreactAgent(client=FakeClient(), plugins=reg, universal_memory=False)
    assert "triage" in _lobes(agent)  # the plugin was applied via the registry


def test_disabled_plugin_in_registry_is_not_applied():
    reg = PluginRegistry([PluginSupportTriage()]).disable("support_triage")
    agent = PreactAgent(client=FakeClient(), plugins=reg, universal_memory=False)
    assert "triage" not in _lobes(agent)


def test_builtin_registry_seeds_infra_plugins():
    reg = builtin_registry()
    assert set(reg.names()) >= {"otel", "guardrails"}
    assert reg.is_enabled("otel") and reg.is_enabled("guardrails")
