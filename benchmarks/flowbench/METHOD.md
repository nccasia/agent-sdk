# Method — flowbench

> The optimization approach for this bench. Standard: `../_shared/TEMPLATE.md`.
> Runnable + deterministic (no provider) — see `README.md`.

## What it certifies
That **every default flow is wired and works** — each routes correctly, runs the right sequence of
canonical reasoning **states** (`understand/explore/plan/act/synthesize/cite/filter/respond`,
docs/concepts/15), maps to a complexity **tier** (direct/standard/deep/steward) whose grounding
contract holds, survives adversarial near-neighbour routing, and actually executes (a `FakeClient`
probe runs the declared stages and answers). One scenario per flow + a coverage check ⇒ no flow goes
untested; the tier check ⇒ the whole simple→complex spectrum is represented.
Modes: routing · tiers · states · grounding · coverage · determinism · subject · execution.

## SDK target (the concept it maps to)
`agent_sdk/flows/` (`Flow`/`FlowStep`/`FlowRegistry`, `defaults.py`, `stages/`), the Blackboard
handoff, and `tool_loop` (`agent_sdk/lobes/runtime.py`). `agent_sdk.inspection.inspect_flow_axis`.

## The lever (optimization approach)
| Failing dimension | Root cause | Lever |
|---|---|---|
| sequencing wrong | flow steps don't match declaration | the flow/stage definitions (data rows in `flows/`) |
| handoff broken | later step doesn't see earlier writes | Blackboard wiring / step `writes` |
| react unbounded / no termination | loop runs away | stage `loop` config, `tool_loop` `max_loops` / `drop_tools_on_final_hop` |
| customize leaks | flow-axis weights alter lobe axis (or vice versa) | the separate `flow_*` weight namespace |

## Metrics & gates (from the monorepo bench)
free: `sequencing`/`customizable`/`handoff`/`react`/`robust` all-pass. live: `taskflow_accuracy >= 0.7`,
`taskflow_grounded >= 0.8`, `flowwalk_accuracy >= 0.8`.
