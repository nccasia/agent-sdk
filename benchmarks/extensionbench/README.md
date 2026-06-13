# extensionbench — plugins as first-class plug-and-play components (LIVE)

The live behavior gate for the SDK's plugin system. It proves that a `Plugin` is a first-class
carrier of the **full capacity surface** — lobes, stages, paths/flows, skills, tools — and that
plugging it in actually changes what the **real agent does** against a live provider.

Like `agentbench`, this is a **LIVE bench — no stubs, no FakeClient.** It builds the real
`PreactAgent` with and without the worked-example plugin (`agent_sdk.plugins.PluginSupportTriage`,
which contributes one capability of every kind) and measures the difference from the real
`probe()` trace.

## Run

```bash
uv --directory packages/agent-sdk run python benchmarks/extensionbench/run.py --live
uv --directory packages/agent-sdk run python benchmarks/extensionbench/run.py --live --report
```

It loads the provider token/model from the repo `.env` (`ANTHROPIC_AUTH_TOKEN`,
`ANTHROPIC_MODEL`). Exit `0` = `READY`, non-zero = `NOT_READY`. There is no offline path — it
only runs live (the deterministic structure is covered by integration tests, below).

## The behavior contract (`dataset/behaviors.jsonl`)

Each line declares a turn and the capabilities it should exercise when plugged in. The runner
probes it through a plugged agent and a bare agent and derives behaviors per scenario `kind`.

**`kind: plugin`** — the full-surface `PluginSupportTriage`:

| behavior | what it asserts (from the real trace) |
|---|---|
| `plugin.path_active` | plugged → the urgent turn routes to the `triage` flow |
| `plugin.lobe_active` | plugged → the `triage` lobe activates |
| `plugin.tool_active` | plugged → the model calls the plugin's `lookup_ticket` tool |
| `unplugged.tool_gone` | no plugin → `lookup_ticket` is not callable, never invoked |
| `unplugged.path_gone` | no plugin → the same turn does NOT route to `triage` |

**`kind: mcp`** — `OrdersPlugin`, a plugin that **owns a dedicated MCP server** + an agentic flow
that uses its discovered tool (the MCP server runs in-process over the transport seam; the
agent/provider is live):

| behavior | what it asserts |
|---|---|
| `mcp.connected` | plugged → the owned MCP server completes the `initialize` handshake (status check) |
| `mcp.tool_discovered` | plugged → `tools/list` discovery registers `order_status` in the engine's tools |
| `mcp.path_active` | plugged → the order turn routes to the plugin's `orders` flow |
| `mcp.tool_called` | plugged → the **live model** calls the MCP-served `order_status` tool |
| `mcp.unplugged_gone` | no plugin → `order_status` is not available and never invoked |

## Where the deterministic checks live

The non-live, structural guarantees — which capability objects register, the pinned
cite/filter/synthesize removal guard, and no-plugin parity — are **integration tests**, not a
bench: `tests/test_plugins_full_surface.py` (runs in `pytest`, no provider).
