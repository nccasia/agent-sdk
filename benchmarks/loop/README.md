# benchmarks/loop — the self-improvement feedback loop

The deterministic backbone the **`improve-loop`** skill drives. It turns the five live benches into a
measurable ratchet: every sweep records a verdict snapshot, so the SDK getting *better and better*
across iterations is visible, not vibes.

```bash
bash benchmarks/loop/ladder.sh          # free gate + all live benches → matrix + trend (appends history)
LOOP_FREE_ONLY=1 bash …/ladder.sh       # just the free gate (no provider) — quick smoke
LOOP_MODEL=MiniMax-M2.7 LOOP_TRIALS=3 bash …/ladder.sh   # pin model/trials for comparable numbers
python3 benchmarks/loop/snapshot.py benchmarks/loop/last-run   # re-print matrix/trend from a sweep
```

- **`ladder.sh`** — runs `ci-free-gates.sh`, then each bench with only the flags it accepts
  (skillbench/agentbench/extensionbench take `--report`/`--model`; skillbench/coding-agent-bench take
  `--trials`; taskbench takes none). Non-invasive: it records exit code + scorecard, never edits a
  bench. Stops if the free gate is red.
- **`snapshot.py`** — parses the sweep into a readiness matrix, appends one record to
  `history.jsonl`, and prints the trend (net READY over the window). Exit `0` only when every measured
  bench is READY — a continue-signal for the loop.
- **`history.jsonl`** — append-only trend (committed). One record per sweep: time, sha, per-bench
  status + checks, summary counts.
- **`last-run/`** — transient per-sweep logs (gitignored).

## The ratchet (why a loop, not a single pass)

A bench that never fails proves nothing (see `../skillbench/ANALYSIS.md`). So the loop alternates two
halves, and the trend should climb:

1. **Raise SDK quality** — close every NOT_READY/UNMEASURED via the `optimize-verdict` skill
   (smallest legitimate fix → re-run → invariants green → commit).
2. **Raise bench rigor** — once green, the `bench-harden` skill adds discriminating cases (near-
   neighbor, refusal, adversarial, per-turn) that expose the next real gap, flipping a bench back to
   NOT_READY *on purpose*. That's success, not regression — it found something real.

Repeat. The history trend is the scoreboard: more benches READY at ever-higher bench rigor.
