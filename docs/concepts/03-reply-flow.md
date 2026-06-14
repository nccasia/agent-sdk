# Reply flow — collectors → the response stage

A PreAct turn is a pipeline of stages. The SDK treats them as **collectors → one response
stage**:

- **Collector stages** (plan / research / tool loops) gather information. Each non-final
  stage's output is carried forward as a labeled **note** (`[stage] …`) — raw chunks never
  cross the boundary, only the compact note.
- **The response stage** renders the **terminal** message. It is the expression domain's
  `respond` lobe (`agent_sdk/expression/lobes/respond.py`) + an optional explicit
  `respond` stage (`agent_sdk/expression/stages/respond.py`). The lobe frames the reply:
  *"you are the assistant continuing this conversation; using the information gathered this
  turn (the notes above) and the conversation so far, write the next reply — continue
  naturally, don't restart or re-greet."*

Grounding is unaffected — `cite`/`filter` still run, so the ground-or-refuse contract holds.

## The framing — two contributions

`RespondLobe.prompt(ctx)` returns **two** `PromptContribution`s, both tagged `respond`:

1. **continuation / conversation-flow rules** (`CONTINUATION`) — continue naturally from the
   notes + the conversation, don't restart or re-greet;
2. **voice + next-step** (`STYLE` + `NEXT_STEP`) — the reply's tone and how to close (end by
   pointing to a relevant next step).

Splitting them lets an override replace just the voice while keeping the continuation contract,
and keeps each part visible in the prompt provenance.

## Two ways to render — flow decides / stage decides

- **Stage pinned by default** — the engine pins the `respond` lobe's framing onto whatever the
  flow's last stage is (placed *after* the notes), so every flow ends by composing a
  continuation reply with **no extra LLM call and no flow surgery**. It auto-injects the
  `respond` lobe if a custom network doesn't define one (`engine.py`).
- **A flow can make it an explicit stage** — list `respond_step("<flow>")`
  (`agent_sdk/expression/stages/respond.py`) as a flow's terminal step. The engine sees the
  terminal already carries the `respond` lobe and renders it there (no double-pin). Like any
  stage it self-gates and accepts per-stage overrides (`apply_stage_overrides`).

## Customize it — override the lobe

The reply renderer is **overridable**: a plugin contributes a lobe with `id="respond"`, which
**replaces** the builtin (plugin-wins by id — the general lobe-override rule). This is how a
platform/bot gives the reply its own voice (e.g. Mezon / agent_core), without new policy fields:

```python
from agent_sdk.expression.lobes.respond import RespondLobe

class MezonRespond(RespondLobe):
    STYLE = "Reply warmly, like a friendly mentor — explain, don't just list."  # tweak a part
    NEXT_STEP = "End with a specific, relevant suggestion to continue."
    # …or override prompt() for full control of the contributions.

class RespondPlugin:
    name = "respond_override"
    def install(self, setup):
        setup.add_lobe(MezonRespond())   # replaces the builtin `respond`

agent = PreactAgent(client=…, plugins=[RespondPlugin()])
```

Override a single part (`CONTINUATION` / `STYLE` / `NEXT_STEP`) and the lobe recomposes the
framing; or override `prompt()` for full control. Removal-protection of the safety floor
(`cite`/`filter`) is unchanged — overriding `respond` is replacement, not removal.

## Continuation, not restart — the trimmed transcript

The agent holds **its own** dialogue (`Session`/`SessionState`) and sends prior user/assistant
turns to the model as native messages, so follow-up turns continue naturally and never re-greet.
The conversation lives **once** — in the `messages` array (Claude-Code style) — so the respond
lobe does **not** re-inject the transcript into the system prompt (de-dup; keeps the cache
prefix stable). Prior turns render as a **trimmed transcript** (primacy + recency) via
`SessionState.messages(first_n, last_m, max_turn_chars)`:

- **n first** turns kept (the task framing) + the rolling `summary` + a
  `[… k earlier turns elided …]` marker, folded into one `[Conversation so far]` block;
- **the middle** blurred (elided / covered by the summary);
- **n last** turns kept as native messages, each **capped** to `max_turn_chars` (the live thread).

A short conversation renders verbatim — trimming only engages once the history grows. The window
is **configurable per agent**: `PreactAgent(…, history_window={"first_n": 1, "last_m": 6,
"max_turn_chars": 2000})` — a platform tunes how much conversation the response stage sees.
