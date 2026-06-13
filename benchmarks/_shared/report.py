"""One consolidated, self-contained HTML report for a whole bench run.

A single scannable page — the verdict, every mode's checks, the headline metrics,
and the probe traces — regenerated to ONE file each run (opt-in via ``--report``).
No JS, no external assets; open it in any browser.
"""

from __future__ import annotations

import html as _html
from pathlib import Path
from typing import Any

__all__ = ["render_consolidated", "write_consolidated"]

_CSS = """
:root{--paper:#FAFAF7;--ink:#0E0E0C;--muted:#6b6b63;--line:#e4e3db;
--emerald:#1F6B4A;--amber:#B8845B;--red:#b3261e;--card:#fff}
*{box-sizing:border-box}
body{background:var(--paper);color:var(--ink);margin:0;
font:14px/1.55 -apple-system,Roboto,Segoe UI,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:22px;margin:0 0 2px}.sub{color:var(--muted);font-size:13px;margin-bottom:18px}
h2{font-size:13px;margin:28px 0 10px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted)}
.verdict{display:inline-block;font-size:15px;font-weight:700;padding:7px 16px;border-radius:10px;
border:1px solid var(--line)}
.verdict.READY{background:#e7f0ea;color:var(--emerald);border-color:#cfe2d6}
.verdict.NOT_READY{background:#fbe9e7;color:var(--red);border-color:#f0cdc7}
.verdict.UNMEASURED{background:#f0f0ea;color:var(--muted)}
.reasons{color:var(--red);font-size:12.5px;margin:8px 0 0}
.cards{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}
.pill{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:9px 13px;min-width:120px}
.pill .k{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.pill .v{font-size:18px;font-weight:600;margin-top:2px;font-family:"Geist Mono",ui-monospace,Menlo,monospace}
table{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:13px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:#f3f2ec;font-weight:600;font-size:11.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.03em}
tr:last-child td{border-bottom:0}
code,.mono{font-family:"Geist Mono",ui-monospace,Menlo,monospace;font-size:12px}
.ok{color:var(--emerald);font-weight:600}.bad{color:var(--red);font-weight:600}
.badge{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;
border:1px solid var(--line);background:#f3f2ec;margin:0 4px 4px 0}
.badge.flow{background:#e7f0ea;border-color:#cfe2d6;color:var(--emerald)}
.badge.t1{background:#e7f0ea;border-color:#cfe2d6;color:var(--emerald)}
.badge.t2{background:#f5ece2;border-color:#e7d6c2;color:var(--amber)}
.badge.t3{background:#f0f0ea;color:var(--muted)}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0;padding:2px 14px}
summary{cursor:pointer;padding:9px 0;font-weight:600;list-style:none}
summary::-webkit-details-marker{display:none}
summary .meta{font-weight:400;color:var(--muted);margin-left:8px}
.detail{color:var(--muted)}
.step{margin:2px 0;font-size:12px}
.step .lbl{display:inline-block;width:58px;color:var(--muted);font-size:10.5px;text-transform:uppercase}
.stage{border-left:2px solid var(--line);margin:8px 0 8px 2px;padding:1px 0 1px 12px}
.ans{background:#f3f2ec;border-radius:8px;padding:9px 11px;margin-top:8px;white-space:pre-wrap;font-size:12.5px}
"""


def _e(x: Any) -> str:
    return _html.escape(str(x))


def _trunc(x: Any, n: int = 160) -> str:
    s = str(x)
    return s if len(s) <= n else s[:n] + "…"


def _is_curve(v: Any) -> bool:
    return (isinstance(v, (list, tuple)) and len(v) >= 2
            and all(isinstance(p, (list, tuple)) and len(p) == 2 for p in v))


def _sparkline(series: list, *, w: int = 220, h: int = 44) -> str:
    """A tiny inline-SVG line over (x, y) points — log-x so 100→100k reads evenly."""
    import math
    xs = [float(p[0]) for p in series]
    ys = [float(p[1]) for p in series]
    lx = [math.log10(max(x, 1)) for x in xs]
    x0, x1 = min(lx), max(lx)
    y0, y1 = min(ys), max(ys)
    def px(i):
        return round(6 + (lx[i] - x0) / (x1 - x0 or 1) * (w - 12), 1)
    def py(i):
        return round(h - 6 - (ys[i] - y0) / (y1 - y0 or 1) * (h - 12), 1)
    pts = " ".join(f"{px(i)},{py(i)}" for i in range(len(series)))
    dots = "".join(f'<circle cx="{px(i)}" cy="{py(i)}" r="2.5" fill="var(--emerald)"/>'
                   for i in range(len(series)))
    return (f'<svg width="{w}" height="{h}" style="background:#fff;border:1px solid var(--line);'
            f'border-radius:8px">'
            f'<polyline points="{pts}" fill="none" stroke="var(--emerald)" stroke-width="1.5"/>'
            f'{dots}</svg>')


def _curve_block(key: str, series: list) -> str:
    spark = _sparkline(series)
    cells = "".join(f"<tr><td class=mono>{_e(p[0])}</td><td class=mono>{_e(p[1])}</td></tr>"
                    for p in series)
    return (f'<div class="pill" style="min-width:240px"><div class="k">{_e(key)}</div>{spark}'
            f'<table style="margin-top:6px"><tr><th>x</th><th>y</th></tr>{cells}</table></div>')


def _metric_cards(metrics: dict) -> str:
    if not metrics:
        return ""
    flat = {k: v for k, v in metrics.items() if not _is_curve(v)}
    curves = {k: v for k, v in metrics.items() if _is_curve(v)}
    cells = "".join(
        f'<div class="pill"><div class="k">{_e(k)}</div><div class="v">{_e(v)}</div></div>'
        for k, v in flat.items()
    )
    cells += "".join(_curve_block(k, v) for k, v in curves.items())
    return f'<div class="cards">{cells}</div>'


def _mode_block(mode: str, payload: dict) -> str:
    checks = payload.get("checks", [])
    n, npass = payload.get("n", len(checks)), payload.get("pass", 0)
    if payload.get("skipped"):
        status = '<span class="meta">UNMEASURED — ' + _e(payload.get("skip_reason", "skipped")) + "</span>"
        cls, open_ = "", ""
    else:
        ok = payload.get("all_pass")
        status = f'<span class="{"ok" if ok else "bad"}">{npass}/{n}</span>'
        cls = "" if ok else "bad"
        open_ = "" if ok else " open"  # auto-expand a failing mode
    rows = "".join(
        f'<tr><td>{"✓" if c["ok"] else "✗"}</td><td class=mono>{_e(c["id"])}</td>'
        f'<td class=detail>{_e(_trunc(c.get("detail", ""), 90))}</td></tr>'
        for c in checks
    )
    table = (f'<table><tr><th></th><th>check</th><th>detail</th></tr>{rows}</table>'
             if rows else '<div class="detail">(no checks)</div>')
    return (f'<details{open_}><summary class="{cls}">{_e(mode)} '
            f'<span class="meta">{status}</span></summary>{table}</details>')


def _probe_block(p: Any) -> str:
    d = p.to_json() if hasattr(p, "to_json") else p
    flow = d.get("flow", "?")
    status = d.get("status", "?")
    tcs = d.get("tool_calls", [])
    tier_counts = (d.get("attention") or {}).get("tier_counts") or {}
    tiers = " ".join(
        f'<span class="badge t{t}">T{t}·{tier_counts.get(str(t), 0)}</span>' for t in (1, 2, 3)
        if tier_counts.get(str(t))
    )
    seq = " → ".join(_e(s.get("stage")) for s in d.get("stages", [])) or "—"
    tools = "".join(
        f'<div class="step"><span class=lbl>→ {_e(c.get("name"))}</span>'
        f'<span class="mono">{_e(_trunc(c.get("input"), 70))}</span> '
        f'<span class=detail>→ {_e(_trunc(c.get("output"), 80))}</span></div>'
        for c in tcs
    )
    ans = f'<div class="ans">{_e(_trunc(d.get("answer", ""), 600))}</div>' if d.get("answer") else ""
    head = (f'<summary>{_e(d.get("label"))} '
            f'<span class="meta"><span class="badge flow">{_e(flow)}</span> '
            f'<span class="{"ok" if status == "answered" else "bad"}">{_e(status)}</span> · '
            f'{len(tcs)} tool calls {tiers}</span></summary>')
    body = f'<div class="mono detail" style="margin:4px 0">flow: {seq}</div>{tools}{ans}'
    return f'<details>{head}{body}</details>'


def render_consolidated(
    *,
    verdict: dict,
    modes: dict[str, dict],
    probes: list,
    label: str = "react-context-bench",
    generated_at: str | None = None,
) -> str:
    status = verdict.get("status", "?")
    reasons = verdict.get("reasons") or []
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{_e(label)} report</title><style>{_CSS}</style></head><body><div class='wrap'>",
        f"<h1>{_e(label)}</h1>",
        f"<div class='sub'>benchmarking the agent-sdk engine's context management · {_e(generated_at or '')}</div>",
        f'<div class="verdict {status}">{_e(status)}</div>',
    ]
    if reasons:
        parts.append('<div class="reasons">' + "<br>".join(_e(r) for r in reasons) + "</div>")
    parts.append(_metric_cards(verdict.get("metrics") or {}))

    parts.append("<h2>Modes</h2>")
    parts.extend(_mode_block(m, p) for m, p in modes.items() if p is not None)

    if probes:
        parts.append("<h2>Probe traces (real turns)</h2>")
        parts.extend(_probe_block(p) for p in probes)

    parts.append("</div></body></html>")
    return "".join(parts)


def write_consolidated(path: str | Path, **kwargs: Any) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_consolidated(**kwargs), encoding="utf-8")
    return out
