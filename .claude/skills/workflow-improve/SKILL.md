---
name: workflow-improve
description: Run the agent-sdk improvement feedback loop autonomously via Claude's Workflow (workflow.js) feature — the improve-loop workflow drives diagnose → implement → re-bench → keep-or-revert per wave, committing kept waves and recording the improve/ ratchet, then hardening a green bench to expose the next gap. Use when the user says "run the workflow", "run the improve-loop workflow", "use workflow.js to improve the SDK", "autonomously improve <bench> over N waves", or wants the multi-agent loop rather than the hands-on improve-loop skill.
---

# workflow-improve — drive the loop with Claude's Workflow feature

Launch the deterministic, multi-agent **improve-loop** workflow (`.claude/workflows/improve-loop.js`).
It is the autonomous, fan-out version of the `improve-loop` skill: the control flow (wave loop,
ratchet keep/revert) is deterministic JS, and each wave step (diagnose / implement / bench / promote)
is a subagent that reuses the `optimize-verdict`, `bench-harden`, and `preact-bench` knowledge.

## How to launch

Call the **Workflow** tool (this requires the user's go-ahead — it spawns subagents, runs live
benches, and commits kept waves):

```
Workflow({ name: "improve-loop", args: { bench: "skillbench", waves: 3, model: "MiniMax-M2.7", harden: true } })
```

`args`:
- `bench` — one of `skillbench` | `taskbench` | `agentbench` | `extensionbench` | `coding-agent-bench` (default `skillbench`).
- `waves` — wave budget (default 3).
- `model` — pin the provider model for comparable numbers across waves (recommended).
- `label` — run label (default `loop`).
- `harden` — when a bench reaches READY, add a discriminating case to expose the next gap (default `true`; set `false` to stop at READY).
- `noImprovementStop` — stop after this many consecutive no-improvement waves (default 2).

The workflow runs in the background and notifies on completion; watch progress with `/workflows`.

## What each wave does (and what it guarantees)

1. **bench** — run the live bench, snapshot the verdict deterministically (`improve_cli verdict-from-log`).
2. **diagnose** — pick the worst failing gating check (or, if READY and `harden`, the weakest
   dimension); name the smallest surface; write `improve/<wave>/diagnosis.md` + `rfc.md`.
3. **implement** — apply the smallest legitimate fix (or add the harder case); run `pytest` + the five
   invariants + `ruff`. If it can't stay green, it **reverts** and the wave is skipped.
4. **bench (after)** — re-run; snapshot `improve/<wave>/after.json`.
5. **promote** — the deterministic ratchet (`improve_cli promote`) keeps the wave only if it ratcheted
   up (status rank + passing-gate count); on keep it **commits** (conventional subject), on revert it
   drops the working-tree change. Either way the append-only `improve/<wave>/` record persists.

Stops at the wave budget or after `noImprovementStop` no-improvement waves. Returns the final verdict
and a per-wave journal; the trend lives in `improve/journal.md` + `improve/history.jsonl`.

## Relationship to the other skills
- **`improve-loop` skill** — the hands-on / `/loop`-paced version of the same ratchet; use it to drive
  one iteration yourself or watch closely.
- **`workflow-improve`** (this) — hand the whole multi-wave loop to the Workflow engine for autonomous,
  resumable, fan-out execution.
- Both honor the same non-negotiables: never weaken a gate, branch the interpreter, stub a bench, or
  break the five invariants — the workflow hard-stops/reverts instead (see
  `../preact-bench/reference/optimization-surfaces.md`).

## Notes
- The ratchet bookkeeping is deterministic (`improve_cli.py`) — the model diagnoses and implements;
  the CLI decides keep/revert and records. No verdict is model-judged.
- To resume after a pause/edit, relaunch with `Workflow({ scriptPath: ".claude/workflows/improve-loop.js", resumeFromRunId: "<id>" })`.
- New benches authored with **bench-scaffold** plug in automatically — pass their name as `bench`.
