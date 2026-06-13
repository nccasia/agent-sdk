# Reply flow — collectors → the response stage

A PreAct turn is a pipeline of stages. The SDK treats them as **collectors → one response
stage**:

- **Collector stages** (plan / research / tool loops) gather information. Each non-final
  stage's output is carried forward as a labeled **note** (`[stage] …`) — raw chunks never
  cross the boundary, only the compact note.
- **The response stage** renders the **terminal** message. It's a real, dedicated `respond`
  lobe (`lobes/expression/respond.py`, registered in the production network) framed: *"you are
  the assistant continuing this conversation; using the information gathered this turn (the
  notes above) and the conversation so far, write the next reply to the user's latest message —
  continue naturally, don't restart or re-greet."*

**Two ways — full customization (flow decides / stage decides):**

- **Stage pinned by default** — the engine pins the `respond` lobe's framing onto whatever the
  flow's last stage is (placed *after* the notes), so every flow ends by composing a
  continuation reply with **no extra LLM call and no flow surgery**. It injects the `respond`
  lobe if a custom network doesn't define one.
- **A flow can make it an explicit stage** — list `respond_step("<flow>")`
  (`flows/stages/respond.py`) as a flow's terminal step in `flows/defaults.py`. The engine sees
  the terminal already carries the `respond` lobe and renders it there (no double-pin). Like any
  stage, it self-gates via `activation`/`signal_weights` (`flows/stages/common.Stage`).

Grounding is unaffected — `cite`/`filter` still run, so the ground-or-refuse contract holds.

## Continuation, not restart

The agent holds **its own** dialogue (`Session`/`SessionState`) and sends prior user/assistant
turns to the model, so follow-up turns continue naturally and never re-greet. Prior turns are
rendered as a **trimmed transcript** (primacy + recency) so a long history doesn't bloat
context — see `SessionState.messages()`:

- **n first** turns kept (the task framing) + the rolling `summary` + a
  `[… k earlier turns elided …]` marker, folded into one `[Conversation so far]` block;
- **the middle** blurred (elided / covered by the summary);
- **n last** turns kept as native messages, each **capped** to `max_turn_chars` (the live
  thread).

A short conversation renders verbatim — trimming only engages once the history grows.
