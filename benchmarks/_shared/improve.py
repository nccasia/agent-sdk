"""improve.py — the moving-baseline ratchet for agent-sdk benches.

Brings the monorepo ``benchmarks/<bench>/improve/`` convention down into the standalone SDK:
a moving baseline (``best.json``) every wave must beat, an append-only per-wave record
(``wave-NNN/``: diagnosis · rfc · diff · before/after · decision), a one-line ``journal.md``,
a longitudinal ``history.jsonl``, and a frozen ``releases/`` + ``SOTA.json`` champion layer.

Generalized over the verdict ``compose_verdict()`` emits — ``{status, reasons, gates, metrics}``
where ``gates`` is ``{mode_all_pass: bool|None}``. The keep/revert decision is DETERMINISTIC
(status rank + count of passing gates), never model-judged — the workflow.js driver and the skills
call this so the ratchet is reproducible. Pure stdlib; no provider, no network.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

_RANK = {"UNMEASURED": 0, "NOT_READY": 1, "READY": 2}
_VERDICT_RE = re.compile(r"verdict\s+(READY|NOT_READY|UNMEASURED)")
_CHECKS_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s+checks pass")
_EXIT_RE = re.compile(r"EXIT=(\d+)")
_NOCREDS_RE = re.compile(r"only runs live|set a provider token|no credentials", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def improve_dir(bench_dir: Path) -> Path:
    d = Path(bench_dir) / "improve"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── verdict extraction (deterministic, from a bench run's stdout log) ─────────
def verdict_from_log(text: str) -> dict:
    """Parse a bench ``run.py`` stdout log into a normalized verdict snapshot.

    Status precedence: an explicit ``verdict <STATUS>`` line wins; else a missing-credentials
    message ⇒ UNMEASURED; else an ``EXIT=<code>`` marker (0 ⇒ READY else NOT_READY). Also pulls
    the ``X/Y checks pass`` scorecard line when present.
    """
    vhits = _VERDICT_RE.findall(text)
    if vhits:
        status = vhits[-1]
    elif _NOCREDS_RE.search(text):
        status = "UNMEASURED"
    else:
        ex = _EXIT_RE.findall(text)
        status = ("READY" if ex[-1] == "0" else "NOT_READY") if ex else "UNMEASURED"
    cm = _CHECKS_RE.findall(text)
    gp, gt = (int(cm[-1][0]), int(cm[-1][1])) if cm else (0, 0)
    return {"status": status, "gates_pass": gp, "gates_total": gt, "stamp": _now()}


def verdict_summary(verdict: dict) -> dict:
    """Normalize a ``compose_verdict()`` dict (or an already-normalized one) to the ratchet shape."""
    if "gates_pass" in verdict:  # already normalized (e.g. from verdict_from_log)
        return {k: verdict[k] for k in ("status", "gates_pass", "gates_total") if k in verdict}
    gates = verdict.get("gates", {}) or {}
    return {
        "status": verdict.get("status", "UNMEASURED"),
        "gates_pass": sum(1 for v in gates.values() if v is True),
        "gates_total": sum(1 for v in gates.values() if v is not None),
    }


# ── the ratchet decision ─────────────────────────────────────────────────────
def delta_gate(before: dict | None, after: dict) -> dict:
    """Keep the wave iff it ratchets UP and nothing regressed.

    Direction-free and unambiguous: compare status rank (UNMEASURED<NOT_READY<READY) and the
    count of passing gates. A wave is kept only if status improved OR more gates pass, and never
    if status dropped or fewer gates pass at the same status. Inputs are ``verdict_summary`` dicts.
    """
    b = verdict_summary(before) if before else {"status": "UNMEASURED", "gates_pass": 0, "gates_total": 0}
    a = verdict_summary(after)
    bstat, astat = b.get("status", "UNMEASURED"), a.get("status", "UNMEASURED")
    bpass, apass = b.get("gates_pass", 0), a.get("gates_pass", 0)
    if _RANK[astat] < _RANK[bstat]:
        return {"kept": False, "reason": f"status regressed {bstat}→{astat}", "before": b, "after": a}
    if _RANK[astat] == _RANK[bstat] and apass < bpass:
        return {"kept": False, "reason": f"fewer gates pass ({bpass}→{apass})", "before": b, "after": a}
    improved = _RANK[astat] > _RANK[bstat] or apass > bpass
    return {
        "kept": improved,
        "reason": "ratchet up" if improved else "no-improvement (hold baseline)",
        "before": b,
        "after": a,
    }


# ── baseline / journal / history ─────────────────────────────────────────────
def load_best(bench_dir: Path) -> dict | None:
    p = improve_dir(bench_dir) / "best.json"
    return json.loads(p.read_text()) if p.exists() else None


def write_best(bench_dir: Path, verdict: dict, *, label: str, model: str = "") -> dict:
    rec = {**verdict_summary(verdict), "label": label, "model": model, "stamp": _now()}
    (improve_dir(bench_dir) / "best.json").write_text(json.dumps(rec, indent=2) + "\n")
    return rec


def append_journal(bench_dir: Path, line: str) -> None:
    p = improve_dir(bench_dir) / "journal.md"
    head = "" if p.exists() else "# improve journal — one line per wave (outcome + reason)\n\n"
    with p.open("a") as f:
        f.write(head + f"- {_now()}  {line}\n")


def append_history(bench_dir: Path, record: dict) -> None:
    with (improve_dir(bench_dir) / "history.jsonl").open("a") as f:
        f.write(json.dumps({"stamp": _now(), **record}) + "\n")


# ── per-wave append-only record ──────────────────────────────────────────────
def next_wave_id(bench_dir: Path) -> str:
    n = 0
    for w in improve_dir(bench_dir).glob("wave-*"):
        m = re.match(r"wave-(\d+)", w.name)
        if m:
            n = max(n, int(m.group(1)))
    return f"wave-{n + 1:03d}"


def new_wave(bench_dir: Path) -> tuple[str, Path]:
    wid = next_wave_id(bench_dir)
    wd = improve_dir(bench_dir) / wid
    wd.mkdir(parents=True, exist_ok=True)
    return wid, wd


def write_wave_decision(wave_dir: Path, decision: dict) -> None:
    (Path(wave_dir) / "decision.json").write_text(json.dumps(decision, indent=2) + "\n")


# ── frozen champion layer (SOTA + releases) ──────────────────────────────────
def releases_dir(bench_dir: Path) -> Path:
    d = Path(bench_dir) / "releases"
    d.mkdir(parents=True, exist_ok=True)
    return d


def snapshot_release(bench_dir: Path, *, label: str, recipe: dict | None = None) -> dict:
    """Freeze the current best.json as an immutable release and update the ledger + SOTA pointer."""
    best = load_best(bench_dir) or {}
    rels = releases_dir(bench_dir)
    ledger_p = rels / "ledger.json"
    ledger = json.loads(ledger_p.read_text()) if ledger_p.exists() else {"releases": []}
    rid = f"release-{len(ledger['releases']) + 1:03d}"
    parent = ledger["releases"][-1]["release_id"] if ledger["releases"] else None
    rel = {"release_id": rid, "parent": parent, "label": label, "stamp": _now(),
           "scores": best, "recipe": recipe or {}, "status": "active"}
    for r in ledger["releases"]:
        if r.get("status") == "active":
            r["status"] = "superseded"
    ledger["releases"].append({"release_id": rid, "parent": parent, "label": label,
                               "status": "active", "status_verdict": best.get("status")})
    (rels / f"{rid}.json").write_text(json.dumps(rel, indent=2) + "\n")
    ledger_p.write_text(json.dumps(ledger, indent=2) + "\n")
    (Path(bench_dir) / "SOTA.json").write_text(
        json.dumps({"champion_release_id": rid, "scores": best, "stamp": _now()}, indent=2) + "\n")
    return rel
