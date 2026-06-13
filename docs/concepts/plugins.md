# Plugins — first-class plug-and-play components

A **plugin** is the SDK's single, composable extension mechanism. It is a *first-class
plug-and-play component*: at assembly time it can contribute the **full capacity surface** an
agent runs on — lobes, stages, paths/flows, skills, and tools — plus event hooks, guardrails,
and seam bindings. Add it to `plugins=[…]` and its capabilities are registered and resolvable;
drop it and they're gone. Nothing else changes.

```python
from agent_sdk import PreactAgent
from agent_sdk.plugins import PluginSupportTriage

agent = PreactAgent(client=…, plugins=[PluginSupportTriage()])
```

## Core vs. extensions

The SDK draws a line between what *every* agent is and what you *add*. The **core** lobes live in
`agent_sdk/lobes/` — the cognition reasoning spine, tools, skills, task, memory, and the reply flow
(`respond`) — and are intrinsic, not toggleable. **Extensions** are plugins under
`agent_sdk/plugins/` composed onto that core. Two are *default-on but toggleable* (their lobes are
part of the production network): `SafetyPlugin` (`cite` / `filter` grounding) and `FormatPlugin`
(channel / language / tone styling). The rest are *opt-in integrations*. When you add a capability,
prefer an extension unless it is genuinely intrinsic to every agent.

## Layout — a folder per plugin

Each builtin lives in its own subpackage under `agent_sdk/plugins/`, so it owns its code and is
managed on its own: `safety/` (`SafetyPlugin` — the `cite`/`filter` grounding lobes), `format/`
(`FormatPlugin` — the `format` styling lobe), `mcp/` (`PluginMCP`), `workspace/` (`PluginWorkspace`
+ the FS drivers), `otel/` (`PluginOTel`), `guardrails/` (`PluginGuardrails`), `support_triage/`
(`PluginSupportTriage` — the worked example). `base.py` holds the `Plugin` protocol + `AgentSetup`;
`registry.py` holds `PluginRegistry`. The default-on extensions are returned by
`default_capability_plugins()` and woven onto the core by `lobes/network.py`.

## Managing plugins — enable / disable / override (`PluginRegistry`)

For more than ad-hoc `plugins=[…]`, use a `PluginRegistry`: register builtin or custom plugins by
`name`, toggle them, override a builtin with your own, then pass the registry straight to the
agent (it installs `registry.active()`):

```python
from agent_sdk.plugins import PluginRegistry, builtin_registry, PluginMCP

reg = builtin_registry()                  # the no-config builtins (otel, guardrails)
reg.register(PluginMCP(spec={...}))       # add a configured plugin (by its name "mcp")
reg.disable("otel")                       # turn one off
reg.override(MyWorkspace())               # replace a builtin by name
agent = PreactAgent(client=…, plugins=reg)

reg.is_enabled("mcp")  # True   ·   reg.names()  ·   reg.active()  → the installed set
```

A plugin is keyed by its `name`; re-registering the same name **overrides** in place. A disabled
name (or a plugin with `enabled = False`) is excluded from `active()`. `PreactAgent(plugins=…)`
accepts a list **or** a registry.

## The contract

```python
@runtime_checkable
class Plugin(Protocol):
    name: str
    def install(self, setup: AgentSetup) -> None: ...
```

A plugin is any object with a `name` and an `install(setup)` method. (An optional
`enabled: bool` attribute, when `False`, makes the agent skip the plugin entirely — the trivial
"disabled ⇒ contributes nothing".) `install` receives an `AgentSetup` builder and fills it:

| Capacity | `AgentSetup` call | What it adds |
|---|---|---|
| Tool | `add_tool(t)` | an `@tool` function or a `ToolRuntime` |
| Lobe | `add_lobe(lobe)` | a context/behavior worker (OY axis) |
| Stage | `add_stage(stage)` | a reusable execution unit |
| Flow | `add_flow(flow)` | an intent pipeline → a **recognizable path** (derived automatically) |
| Path | `add_path(path)` | an explicit `PathSpec` recognizer (advanced) |
| Skill | `add_skill(skill)` | procedural knowledge, progressively disclosed |
| MCP server | `add_mcp_server(spec)` *or* the `mcp_servers` attribute | a full MCP server (one or many) the plugin owns — connected + discovered in the resolve phase |
| Hooks | `on_event` / `add_pre_check` / `add_post_check` / `add_prefetch_hook` / `add_tool_filter` | observability + guardrails |
| Seam | `bind_workspace(ws)` | a filesystem driver |

A plugin may also **subtract** a builtin it owns or overrides:

```python
setup.remove_lobe(id)    setup.remove_path(name)    setup.remove_flow(name)    setup.remove_skill(slug)
```

## How assembly resolves it

`PreactAgent` builds its `Engine` from the default (or your explicit) building blocks, then folds
in every plugin's contributions, then honors removals:

1. Resolve builtins: `lobes`, `stages`, `flows` (+ the ported production `paths`), `skills`.
2. For each plugin in `plugins=[…]` (skipping any with `enabled = False`), call `install(setup)`.
3. Extend the resolved lists with `setup.lobes/stages/flows/skills/tools`.
4. **Derive a `PathSpec`** for each plugin-added flow so its intent is recognized — the default
   network ships explicit ported recognizers, so plugin flows are compiled and appended.
5. Apply removals: drop named paths/flows and removed skills; drop removed lobes **except pinned
   ones**.

Because step 2 is a no-op when `plugins` is empty, an agent with no plugins is **byte-identical**
to the default network — the parity gate.

## MCP servers — a plugin owns its own (one or many)

An MCP server is **part of a plugin's capability surface**, not a separate plugin. A plugin
that needs a dedicated MCP — or several — just **declares them**, and they all resolve and
register under that one plugin. You never compose `[MyPlugin(), PluginMCP(...), PluginMCP(...)]`
for what is conceptually one unit:

```python
class WeatherSuite:
    name = "weather_suite"
    # declarative: this plugin OWNS these MCP servers (one or many)
    mcp_servers = [
        {"name": "current",  "transport": "http", "endpoint": "https://…/current/mcp"},
        {"name": "forecast", "transport": "http", "endpoint": "https://…/forecast/mcp"},
    ]
    def install(self, setup):
        ...  # its lobes / stages / flows / skills / local tools, as usual

agent = PreactAgent(client=…, plugins=[WeatherSuite()])
await agent.connect()   # {"current": True, "forecast": True}
```

Equivalently, register them imperatively inside `install` (for dynamic/conditional servers):

```python
    def install(self, setup):
        setup.add_mcp_server(spec)            # call once per server — any number
        setup.add_mcp_server(other, transport=embedded_handler)
```

`PluginMCP(spec=…)` remains a convenience for mounting a **standalone** server when you don't
have a plugin of your own; and `PreactAgent(mcp_servers=[…])` mounts servers directly without
any plugin. All three paths feed the same resolve phase.

The phase, per server (`agent_sdk/mcp.py`, `MCPToolRuntime`):

1. **connect / status** — JSON-RPC `initialize` handshake; a server that doesn't answer is
   marked not-connected (`.error` set) and the turn proceeds without it (no crash).
2. **discover schema** — if connected, `tools/list` returns the server's tools.
3. **build tool specs** — each becomes an Anthropic-compatible spec (`inputSchema` →
   `input_schema`).
4. **register downstream** — the specs flow through the normal `CompositeToolRuntime`, so the
   model is offered the tools and `tools/call` executes them — identical to any other tool.

Transport is pluggable: the default speaks JSON-RPC over HTTP (`httpx`); an embedded/in-process
server or a test double is wired with `PluginMCP(spec=…, transport=<async req→resp>)` /
`AgentSetup.add_mcp_server(spec, transport=…)`. The resolve phase is idempotent and runs all
servers concurrently.

## The pinned guard (citations-mandatory)

The output-contract lobes — `cite` and `filter` (`PINNED_LOBES`) and `synthesize`
(`spec.pinned`) — are **never removed**, even if a plugin calls `setup.remove_lobe("cite")`. A
plugin can reshape the network, but it can never *strip* the SDK's ground-or-refuse guarantee.

Note the difference between *stripping* and *disabling*: `cite`/`filter` ship in the default-on
`SafetyPlugin`, so an integrator may deliberately turn grounding off for a non-RAG agent
(`reg.disable("safety")`) — at which point citations-mandatory becomes the caller's responsibility.
What no *third-party* plugin can do is remove them out from under an agent that has them enabled.

## Worked example

`agent_sdk.plugins.PluginSupportTriage` carries one capability of every kind — a `triage` lobe,
a `triage` stage, a `triage` flow (recognized on urgency cues like *urgent / incident / escalate
/ outage*), a `triage_policy` skill, and a `lookup_ticket` tool. Plug it in and an urgent ticket
turn routes to triage:

```python
agent = PreactAgent(client=…, plugins=[PluginSupportTriage()])
agent.inspect("this incident is urgent, escalate ticket 412").path   # → ("triage", …)
```

Without the plugin the same query resolves to an emergent/default path — the capability simply
isn't there.

## Verifying plugin behavior

The behavior contract is gated two ways:

- **Integration tests (deterministic, no provider):** `tests/test_plugins_full_surface.py` —
  per-capacity plug/unplug *structure*, the pinned cite/filter/synthesize removal guard, and
  no-plugin parity. Runs in `pytest`.
- **Live bench:** `benchmarks/extensionbench/` — proves plugging changes what the *real agent*
  does against a live provider (the urgent turn routes to `triage`, lights the lobe, and calls
  `lookup_ticket`; unplugged it doesn't). Run with
  `python benchmarks/extensionbench/run.py --live` (emits `READY`/`NOT_READY`). Like all SDK
  benches, it is **live-only — no stubs**.
