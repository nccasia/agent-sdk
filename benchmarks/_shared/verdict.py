"""Verdict composition — READY / NOT_READY / UNMEASURED over the free modes.

Composes from the modes' in-memory payloads (no result files on disk): a *missing*
mode is UNMEASURED (no evidence is never READY); each mode gates on its ``all_pass``;
a few headline metrics are recorded for transparency. Deterministic gates decide the
status — there is no LLM tier here to rescue a red gate.
"""

from __future__ import annotations

__all__ = ["compose_verdict"]


def compose_verdict(payloads: dict[str, dict | None], *, record: dict[str, list[str]] | None = None) -> dict:
    """Compose a verdict from ``{mode: payload}`` (``None`` = the mode didn't run).

    ``record`` optionally maps a mode → metric names to surface (from its
    ``metrics`` block) without gating. Returns ``{status, reasons, gates, metrics}``.
    """
    record = record or {}
    reasons: list[str] = []
    missing: list[str] = []
    gates: dict = {}
    metrics: dict = {}

    for mode, payload in payloads.items():
        if payload is None:
            missing.append(mode)
            continue
        if payload.get("skipped"):
            # ran but had nothing to measure (e.g. live without a tool loop)
            gates[f"{mode}_all_pass"] = None
            continue
        ok = bool(payload.get("all_pass"))
        gates[f"{mode}_all_pass"] = ok
        if not ok:
            failed = [c["id"] for c in payload.get("checks", []) if not c["ok"]]
            reasons.append(f"{mode}: {len(failed)} failing — {failed[:5]}")
        for name in record.get(mode, []):
            val = (payload.get("metrics") or {}).get(name)
            if val is not None:
                metrics[f"{mode}.{name}"] = val

    status = "UNMEASURED" if missing else ("NOT_READY" if reasons else "READY")
    if missing:
        reasons.insert(0, f"missing evidence: {', '.join(missing)}")
    return {"status": status, "reasons": reasons, "gates": gates, "metrics": metrics}
