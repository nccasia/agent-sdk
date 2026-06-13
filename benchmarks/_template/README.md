# <benchname>

> One paragraph: the capability this bench certifies and why it has its own exam. See `METHOD.md`
> for the optimization approach + metrics, and `../_shared/TEMPLATE.md` for the standard.

LIVE-only (no stubs). Run from the `packages/agent-sdk` dir:

```bash
python benchmarks/<benchname>/run.py --live --report [--trials 3] [--model <id>] [--label base]
```

Exits `0` iff the verdict is READY. Free deterministic checks run first; the live tier needs provider
creds (auto-loaded from `.env` via `_shared/provider.py`).

## Modes
- `<free-mode>` — deterministic; <what it asserts>.
- `<live-mode>` — real provider; <what it measures>, gated at <thresholds from METHOD.md>.

## Feedback loop
This bench plugs into the standard ratchet (`improve/best.json` + `wave-NNN/`, driven by
`improve_cli.py` and the `improve-loop` workflow). Diagnose a failing gate → tune the lever named in
`METHOD.md` → re-bench → keep only if it ratchets up. When green, harden it (`bench-harden`).
