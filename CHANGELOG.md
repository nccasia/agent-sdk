# Changelog

All notable changes to agent-sdk are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) from 1.0 onward.

## [Unreleased]

### Added
- **Plugin/extension system.** First-class plug-and-play plugins carrying the full capacity surface
  (lobes / stages / flows / skills / tools), each in its own folder under `agent_sdk/plugins/`.
- **`PluginRegistry`** — register / override / enable / disable plugins by name; accepted directly
  by `PreactAgent(plugins=…)`. `builtin_registry()` seeds the no-config builtins.
- **MCP support** — a plugin can own one or many MCP servers (declared or registered in `install`);
  they are connected, discovered (`tools/list`), and registered as tools during the resolve phase.
  `PluginMCP` and `PreactAgent(mcp_servers=…)` mount standalone servers.
- **Reply flow** — a pinned `respond` lobe on the terminal stage renders the next message as a
  continuation (no re-greet) from gathered notes; trimmed transcript (primacy + recency);
  XML-tagged context sections by default.
- **Built-in extensions** — `SafetyPlugin`, `FormatPlugin`, `PluginWorkspace`, `PluginMCP`,
  `PluginOTel`, `PluginGuardrails`, and the `PluginSupportTriage` worked example.
- Apache-2.0 license, PyPI metadata, contributing guide.

### Changed
- **Core / extension boundary.** `agent_sdk/lobes/` now holds only the lobes intrinsic to every
  agent (cognition, tools, skills, task, memory, reply) plus the framework and path recognizers.
  Grounding (`cite` / `filter`) and styling (`format`) moved to the default-on but toggleable
  `SafetyPlugin` / `FormatPlugin` extensions. The default network is byte-identical (same 19 lobes,
  same canonical order).

## [0.1.0]

- Initial implementation of the PreAct SDK: the `PreactAgent` façade, the `Engine` kernel,
  first-class `Stage` / `Flow` / `Skill`, `@tool`, multi-provider clients (Anthropic, OpenAI,
  MiniMax, fake), Session/Memory stores, metacognition, serving, the serializable `PreactSpec`, and
  the probe/inspect/bench surface.
