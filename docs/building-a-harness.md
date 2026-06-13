# Building an agent harness on the SDK

The SDK gives you the **building blocks** of the project's reasoning model,
**PreAct** ([`preact.md`](./preact.md)); a *harness* assembles them for a
concrete agent. This walkthrough shows the parts and how they fit. (The
production harness is `BotPolicyInterpreter` in the project; the Mezon bot wires
its instances in `agent_core/lobes/__init__.py` and `agent_core/flows/__init__.py`.)

> Mental model (PreAct = *pre-structured acting*): **OY lobes** decide *what
> context fires*; **OX flows** decide *how action progresses*; **metacognition**
> watches and nudges. The LLM acts inside this scaffold rather than free-form
> tool-calling. See [`preact.md`](./preact.md) and
> [`../../../docs/architecture.md`](../../../docs/architecture.md).

## 1. The one seam you must implement: `LlmCall`

Every lobe behavior and every agentic step calls the LLM through one injectable
protocol, so behaviors are testable with a fake and provider-agnostic.

```python
from agent_core.sdk import LlmCall  # Protocol

async def my_llm(*, stage, system, messages, max_tokens,
                 temperature=None, tools=None, count_usage=True):
    resp = await client.messages.create(
        model=model_for(stage), system=system, messages=messages,
        max_tokens=max_tokens, temperature=temperature or 0, tools=tools or [],
    )
    return resp  # an Anthropic-style message (has .content, .stop_reason)
```

Bundle it (and any optional side-effects) into `LobeServices`:

```python
from agent_core.sdk import LobeServices
services = LobeServices(llm=my_llm, embed=my_embed_fn)  # execute_tools/redis/emit optional
```

## 2. Author lobes (the OY axis)

A lobe is a small, self-describing context worker. Subclass `Lobe`, declare its
metadata + one deterministic, free activation signal, and (optionally) a system
prompt for behaviors that call the LLM.

```python
from agent_core.sdk import Lobe

class Classify(Lobe):
    id = "classify"
    name = "Classify"
    description = "Route the turn simple vs complex."
    use_when = "every answer-producing turn"
    layer = 4                      # B4 cognition (see LAYER_* in network.activation)
    behavior = "select"
    writes = ("route",)            # what it puts on the blackboard
    system_prompt = "Decide if this needs research. Answer SIMPLE or COMPLEX."

    def activation(self, ctx: dict) -> float:
        return 1.0                 # always considered; 0.0 = dark unless pinned
```

Pin the output-contract lobes (`cite`, `filter`) with `pinned = True` — the
activation network can never deactivate them (`PINNED_LOBES`).

## 3. Author flows (the OX axis)

A flow is a named pipeline of steps; each step names the lobe slice it consults
and its loop mode.

```python
from agent_core.sdk import Flow, FlowStep

QNA = Flow(name="qna", steps=(
    FlowStep(name="synthesize", lobes=("classify", "synthesize", "cite", "filter"),
             loop="single"),
))
RESEARCH = Flow(name="research", steps=(
    FlowStep(name="plan",       lobes=("plan",),                 loop="single"),
    FlowStep(name="research",   lobes=("research",), loop="agentic", tools=("kb.search",)),
    FlowStep(name="synthesize", lobes=("synthesize","cite","filter"), loop="single"),
))
```

Loop modes: `none` (pure prompt), `single` (one LLM call), `agentic` (a
`tool_loop`).

## 4. Bind instances to the framework (the default-provider hooks)

The registries are framework — they never import your lobes/flows. You inject
them once at startup:

```python
from agent_core.sdk import (
    set_default_providers, set_default_flows, LobeRegistry, FlowRegistry,
)

set_default_providers(
    lobe_objects=lambda: [Classify(), Synthesize(), Cite(), Filter(), ...],
    paths=lambda: [QNA_PATH, RESEARCH_PATH, ...],     # PathSpec recognizers
)
set_default_flows(lambda: [QNA, RESEARCH])

lobes = LobeRegistry()    # resolves your network
flows = FlowRegistry()    # resolves your flows
```

## 5. Drive a turn

```python
from agent_core.sdk import (
    TurnContext, recognize_paths, resolve_path, tool_loop,
)

# (a) recognize the intent — free, deterministic, never an LLM
scores = recognize_paths(query, lobes.paths(), ctx=signal_ctx)
path = resolve_path(scores) or "qna"

# (b) pick the flow and run its steps
ctx = TurnContext(query=query, services=services, active_path=path)
for step in flows.steps_for_path(path):
    nodes = flows.compose_step_prompt(step, ctx, lobes)   # lobe axis → ContextNodes
    system = render(nodes)                                # your prompt assembly
    if step.loop == "agentic":
        msg, answer = await tool_loop(call, messages=msgs, tools=tool_specs,
                                      execute_tools=run_tools,
                                      assistant_content=to_blocks, max_loops=6)
    elif step.loop == "single":
        msg = await services.llm(stage=step.name, system=system,
                                 messages=msgs, max_tokens=1024, temperature=0)
```

The **Blackboard** (`network.activation.Blackboard`) is the turn-scoped node pool
lobes write to and steps read from; it rejects raw KB chunks (the compression
invariant — only memos cross step boundaries).

## 6. Add metacognition (optional)

```python
from agent_core.sdk import MetaController
from agent_core.sdk.inspection import inspect_lobe_axis, inspect_flow_axis, snapshot_engine

meta = MetaController.from_policy(policy)   # observe (floor) or apply
decision = meta.plan_next(
    lobe_axis=inspect_lobe_axis(lobes, ctx),
    flow_axis=inspect_flow_axis(flows, path),
    target_flow=path, target_step=step.name, current_lobes=step.lobes,
)
# decision.action ∈ {continue, adjust_lobe_slice, retry_step, skip_step, meta_review}
# cite/filter are pinned: never skippable.
```

## 7. Long tool loops: the ReAct funnel (optional)

For agentic steps with many hops, pass a `retier` to `tool_loop` so the working
set funnels toward the answer instead of accumulating:

```python
from agent_core.sdk import tier_observations
await tool_loop(call, ..., retier=lambda msgs, hop: tier_observations(msgs, hop))
```

## Extending later — rows, not branches

A new capability is a **registry row**, never a new `if` in the driver:

```python
lobes.add_row({"id": "summarize", "layer": 4, "behavior": "compose",
               "signals": {...}, "edges": {"cite": 0.4}, "writes": ("memo",)})
flows.add_row({"name": "digest", "steps": [{"name": "synthesize", "lobes": ["summarize", "cite", "filter"]}]})
```

Every mutation re-validates the forward DAG and pinned-edge protection.

## What stays in the project (not the SDK)

The SDK deliberately omits anything bot-specific: the concrete lobes/flows above
are *examples* — the real ones, plus the KB/MCP `ToolRuntime` implementations,
the rag/arag adapters, golden/refusal rules, and `BotPolicyInterpreter`, live in
the project (`agent_core/`), which is free to import `rag_core`/`arag_core`.
