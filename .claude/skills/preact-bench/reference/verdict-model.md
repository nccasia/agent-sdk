# The verdict model — READY / NOT_READY / UNMEASURED

Every bench composes its verdict the same way, in `benchmarks/_shared/verdict.py:compose_verdict`:

```python
compose_verdict(payloads) -> {"status": str, "reasons": [...], "gates": {...}, "metrics": {...}}
```

- Each **mode/group** produces a payload `{checks: [{id, ok, detail, diag}], n, pass, all_pass, metrics, skipped}`.
- A mode gates on its `all_pass`. `gates[f"{mode}_all_pass"]` is `True`/`False`, or `None` if skipped.
- **status** is decided purely by deterministic gates — there is no LLM judge to rescue a red gate:
  - `UNMEASURED` — a required mode produced no evidence (it never ran). Absence of measurement is
    **never** READY. This is a real failure state to resolve, not a pass.
  - `NOT_READY` — at least one gating check failed. `reasons` lists them (`"{mode}: N failing — [ids]"`).
  - `READY` — all required modes measured and every gating check passed.

## Gates vs diagnostics — the distinction that matters

- **Gating checks** (`diag=False`) decide the verdict. These are what you fix.
- **Diagnostic rows** (`diag=True`) are shown in the report and scorecard but **never gate**. They
  exist because some signals are genuinely flappy across model runs (e.g. skillbench's
  `disclosure_ratio` swings 0.18–0.42 on identical inputs; `activation.overreach_scenarios`). Watch
  trends, but **do not chase a diagnostic** as if it were a failing gate — that's how you waste
  iterations tuning noise. When a diagnostic and a gate disagree, the gate is truth.

## Exit codes & output
- `run.py` exits `0` iff `status == "READY"`, else `1`. Use the exit code as the coarse signal and
  parse the scorecard for the detail.
- The scorecard prints `ok  ` / `FAIL` per check with a short detail, then per-skill/per-capability
  verdicts, then a final `… : X/Y checks pass · verdict <STATUS>` line.

## Provider env (live tier)
`benchmarks/_shared/provider.py:load_provider()` loads `.env` — **SDK-local
`packages/agent-sdk/.env` first**, then the repo-root `.env` — and resolves the model:
- Reads `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY` (+ optional `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`).
- Bridges MiniMax-native `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` onto the `ANTHROPIC_*` the clients
  read (MiniMax speaks the Anthropic protocol, mounted under `…/anthropic`).
- Returns `None` if no creds → the bench prints a clean "only runs live" message and exits. If you
  see that, the creds didn't load; surface it — never stub the provider to get a number.
- Default model when creds present but unpinned: `MiniMax-M2.7` (if MiniMax) else `claude-opus-4-6`.
  Pin with `--model` for reproducibility across a tuning session.
