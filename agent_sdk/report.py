"""One self-contained HTML report — benchmark scorecard + probe internals.

``render_html`` produces a single, dependency-free HTML page combining a
:class:`~agent_sdk.bench.Report` (scenario pass/fail + scores) and any
:class:`~agent_sdk.probe.ProbeRecord` turns (the recognized flow, per-lobe
activation, and the stage-by-stage ReAct timeline). No JS, no external assets —
open the file in any browser. ``write_html`` writes it to disk.

    from agent_sdk.bench import Harness, Scenario
    from agent_sdk.probe import probe
    from agent_sdk.report import write_html

    report = await Harness(agent).run([...])
    probes = [await probe(agent, "add a multiply function")]
    write_html("results/coding-agent.html", "coding-agent-bench",
               report=report, probes=probes)
"""

from __future__ import annotations

import html as _html
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["render_html", "write_html"]

_CSS = """
:root{--paper:#FAFAF7;--ink:#0E0E0C;--muted:#6b6b63;--line:#e4e3db;
--emerald:#1F6B4A;--amber:#B8845B;--red:#b3261e;--card:#fff}
*{box-sizing:border-box}
body{background:var(--paper);color:var(--ink);margin:0;
font:14px/1.5 -apple-system,Roboto,Segoe UI,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:22px;margin:0 0 2px}.sub{color:var(--muted);font-size:13px;margin-bottom:18px}
h2{font-size:15px;margin:30px 0 10px;letter-spacing:.02em;text-transform:uppercase;color:var(--muted)}
.cards{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}
.pill{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:120px}
.pill .k{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.pill .v{font-size:20px;font-weight:600;margin-top:2px}
table{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:#f3f2ec;font-weight:600;font-size:12px;color:var(--muted)}
tr:last-child td{border-bottom:0}
code,.mono{font-family:"Geist Mono",ui-monospace,Menlo,monospace;font-size:12px}
.ok{color:var(--emerald);font-weight:600}.bad{color:var(--red);font-weight:600}
.badge{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;
border:1px solid var(--line);background:#f3f2ec;margin:0 4px 4px 0}
.badge.flow{background:#e7f0ea;border-color:#cfe2d6;color:var(--emerald)}
.badge.tool{background:#f5ece2;border-color:#e7d6c2;color:var(--amber)}
.badge.on{background:#e7f0ea;color:var(--emerald);border-color:#cfe2d6}
.badge.off{color:var(--muted)}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:10px 0;padding:4px 14px}
summary{cursor:pointer;padding:8px 0;font-weight:600;list-style:none}
summary::-webkit-details-marker{display:none}
summary .meta{font-weight:400;color:var(--muted);margin-left:8px}
.stage{border-left:2px solid var(--line);margin:10px 0 10px 4px;padding:2px 0 2px 14px}
.stage>.h{font-weight:600;margin-bottom:4px}
.step{margin:3px 0;font-size:12.5px}
.step .lbl{display:inline-block;width:64px;color:var(--muted);font-size:11px;text-transform:uppercase}
.step.think .t{color:var(--muted)}
.step.tool .t{color:var(--amber)}
.step.result .t{color:var(--ink)}
.step.answer .t{color:var(--emerald)}
.ans{background:#f3f2ec;border-radius:8px;padding:10px 12px;margin-top:8px;white-space:pre-wrap}
.fail{color:var(--red);font-size:12px}
.empty{color:var(--muted);font-style:italic}
.verdict{display:inline-block;padding:6px 16px;border-radius:99px;font-weight:700;
font-size:15px;letter-spacing:.04em;margin:8px 0 2px}
.verdict.READY{background:#e7f0ea;color:var(--emerald);border:1px solid #cfe2d6}
.verdict.NOT_READY{background:#fbe9e7;color:var(--red);border:1px solid #f0cdc8}
.verdict.UNMEASURED{background:#f3f2ec;color:var(--muted);border:1px solid var(--line)}
.reasons{color:var(--red);font-size:12.5px;margin:4px 0 8px}
td.diag{color:var(--muted);font-style:italic}
.tabs{position:relative}
input.tabradio{position:absolute;opacity:0;width:0;height:0}
.tabnav{display:flex;gap:4px;border-bottom:2px solid var(--line);margin:18px 0 16px;flex-wrap:wrap}
.tablabel{cursor:pointer;padding:8px 16px;font-weight:600;color:var(--muted);
border:1px solid transparent;border-bottom:none;border-radius:8px 8px 0 0;margin-bottom:-2px}
.tablabel:hover{color:var(--ink)}
.tabpanel{display:none}
"""


def _e(x: Any) -> str:
    return _html.escape(str(x))


def _trunc(x: Any, n: int = 220) -> str:
    s = str(x)
    return s if len(s) <= n else s[:n] + "…"


def _scorecard(report: Any, probes: list) -> str:
    pills: list[tuple[str, str, str]] = []
    if report is not None:
        s = report.summary()
        cls = "ok" if s["passed"] == s["scenarios"] else "bad"
        pills.append(("scenarios", f"{s['passed']}/{s['scenarios']}", cls))
        pills.append(("path acc", f"{s['path_accuracy']:.0%}", ""))
        if s.get("lobe_recall") is not None:
            pills.append(("lobe recall", f"{s['lobe_recall']:.0%}", ""))
        pills.append(("p95", f"{s['p95_latency_ms']:.0f}ms", ""))
    if probes:
        toks = sum((p.usage or {}).get("output_tokens", 0) for p in probes)
        pills.append(("probes", str(len(probes)), ""))
        pills.append(("out tokens", str(toks), ""))
    cells = "".join(
        f'<div class="pill"><div class="k">{_e(k)}</div>'
        f'<div class="v {cls}">{_e(v)}</div></div>'
        for k, v, cls in pills
    )
    return f'<div class="cards">{cells}</div>'


def _scenarios(report: Any) -> str:
    rows = []
    for r in report.results:
        mark = '<span class="ok">PASS</span>' if r.passed else '<span class="bad">FAIL</span>'
        fail = f'<div class="fail">{_e("; ".join(r.failures))}</div>' if r.failures else ""
        lobes = " ".join(f'<span class="badge on">{_e(x)}</span>' for x in r.activated_lobes[:8])
        rows.append(
            f"<tr><td class=mono>{_e(_trunc(r.scenario.input, 70))}</td>"
            f"<td>{_e(r.scenario.expect_path or '—')}</td>"
            f'<td><span class="badge flow">{_e(r.path[0])}</span> '
            f'<span class="mono">{r.path[1]:.2f}</span></td>'
            f"<td>{lobes}</td><td>{mark}{fail}</td></tr>"
        )
    return (
        "<table><tr><th>input</th><th>expect</th><th>routed flow</th>"
        f"<th>activated lobes</th><th>result</th></tr>{''.join(rows)}</table>"
    )


def _kv(d: dict) -> str:
    items = [f"{k}={v}" for k, v in (d or {}).items() if v]
    return ", ".join(items) if items else "—"


def _edges(d: dict) -> str:
    items = [f"{k}→{v}" for k, v in (d or {}).items()]
    return ", ".join(items) if items else "—"


def _lobe_table(lobes: list[dict]) -> str:
    rows = []
    for lb in lobes:
        on = lb.get("activated")
        rows.append(
            f'<tr><td class=mono>{_e(lb.get("id"))}</td><td>{_e(lb.get("layer"))}</td>'
            f'<td><span class="badge {"on" if on else "off"}">{"yes" if on else "no"}</span></td>'
            f'<td class=mono>{lb.get("activation", 0):.2f}</td>'
            f'<td class=mono>{_e(lb.get("reason"))}</td>'
            f'<td class=mono>{_e(_kv(lb.get("signals")))}</td>'
            f'<td class=mono>{_e(_edges(lb.get("in_edges")))}</td></tr>'
        )
    return (
        "<table><tr><th>lobe</th><th>layer</th><th>activated</th><th>activation</th>"
        f"<th>reason</th><th>signals</th><th>edges</th></tr>{''.join(rows)}</table>"
    )


def _hotspots(hints: list[dict]) -> str:
    if not hints:
        return ""
    rows = []
    for h in hints:
        patch = ", ".join(f"{k}={v}" for k, v in (h.get("weight_patch") or {}).items())
        rows.append(
            f'<tr><td>{_e(h.get("axis"))}</td><td class=mono>{_e(h.get("target"))}</td>'
            f'<td>{_e(h.get("reason"))}</td><td class=mono>{_e(patch)}</td></tr>'
        )
    return (
        '<details><summary>optimization hotspots '
        f'<span class="meta">({len(hints)})</span></summary>'
        "<table><tr><th>axis</th><th>target</th><th>reason</th><th>weight patch</th></tr>"
        f"{''.join(rows)}</table></details>"
    )


def _stage_block(stage: dict) -> str:
    name = _e(stage.get("stage"))
    loop = _e(stage.get("loop", ""))
    lobes = " ".join(f'<span class="badge">{_e(x)}</span>' for x in stage.get("lobes", []))
    if stage.get("skipped"):
        return f'<div class="stage"><div class="h">{name} <span class=empty>(skipped)</span></div></div>'
    steps_html = []
    for st in stage.get("steps", []):
        kind = st.get("kind")
        if kind == "thinking":
            steps_html.append(f'<div class="step think"><span class=lbl>think</span>'
                              f'<span class="t">{_e(_trunc(st.get("text"), 240))}</span></div>')
        elif kind == "tool_use":
            steps_html.append(f'<div class="step tool"><span class=lbl>→ tool</span>'
                              f'<span class="t mono">{_e(st.get("name"))}({_e(_trunc(st.get("input"), 110))})</span></div>')
        elif kind == "tool_result":
            steps_html.append(f'<div class="step result"><span class=lbl>← result</span>'
                              f'<span class="t mono">{_e(_trunc(st.get("output"), 160))}</span></div>')
        elif kind == "answer" and st.get("text"):
            steps_html.append(f'<div class="step answer"><span class=lbl>answer</span>'
                              f'<span class="t">{_e(_trunc(st.get("text"), 300))}</span></div>')
    body = "".join(steps_html) or '<div class="step"><span class=empty>(no LLM step)</span></div>'
    return (
        f'<div class="stage"><div class="h">{name} '
        f'<span class="badge">{loop}</span> {lobes}</div>{body}</div>'
    )


def _runner_up(path: dict) -> str:
    ru = (path or {}).get("runner_up") or {}
    if ru.get("name"):
        return f' <span class="meta mono">(runner-up: {_e(ru["name"])} {ru.get("score", 0):.2f})</span>'
    return ""


def _raw_json(p: Any) -> str:
    blob = json.dumps(p.to_json(), ensure_ascii=False, indent=2, default=str)
    return f"<details><summary>raw JSON</summary><pre class=mono>{_e(blob)}</pre></details>"


def _probe(p: Any) -> str:
    status_cls = "ok" if p.status == "answered" else "bad"
    toks = (p.usage or {}).get("output_tokens", 0)
    seq = " → ".join(_e(s.get("stage")) for s in p.stages) or "—"
    head = (
        f'<summary>{_e(p.label)}'
        f'<span class="meta"><span class="badge flow">{_e(p.flow)} {p.flow_score:.2f}</span>'
        f'{_runner_up(p.path)} '
        f'<span class="{status_cls}">{_e(p.status)}</span> · {toks} out-tok · '
        f'{len(p.tool_calls)} tool calls</span></summary>'
    )
    if p.error:
        body = f'<div class="fail">{_e(p.error)}</div>'
    else:
        timeline = "".join(_stage_block(s) for s in p.stages)
        lobes = f"<details><summary>lobe activation (OY)</summary>{_lobe_table(p.lobes)}</details>"
        ans = f'<div class="ans">{_e(_trunc(p.answer, 1200))}</div>' if p.answer else ""
        body = (
            f'<div class="mono" style="color:var(--muted);margin:6px 0">flow (OX): {seq}</div>'
            f"{timeline}{lobes}{_hotspots(p.hints)}{ans}{_raw_json(p)}"
        )
    return f"<details open>{head}{body}</details>"


def _overview(verdict: dict, modes: dict | None) -> str:
    """The verdict + per-group overview (the report half): a READY/NOT_READY/
    UNMEASURED badge, the failing reasons, metric pills, and one expandable check
    table per group. ``modes`` is ``{group: payload}`` with payload
    ``{checks, n, pass, all_pass}`` (a ``check`` is ``{id, ok, detail, diag?}``)."""
    status = str(verdict.get("status", "?"))
    parts = [f'<div class="verdict {status}">{_e(status)}</div>']
    reasons = [r for r in (verdict.get("reasons") or []) if r]
    if reasons:
        parts.append('<div class="reasons">' + "<br>".join(_e(r) for r in reasons) + "</div>")
    metrics = verdict.get("metrics") or {}
    if metrics:
        pills = "".join(
            f'<div class="pill"><div class="k">{_e(k)}</div><div class="v">{_e(v)}</div></div>'
            for k, v in metrics.items()
        )
        parts.append(f'<div class="cards">{pills}</div>')
    for mode, payload in (modes or {}).items():
        if not payload:
            continue
        checks = payload.get("checks", [])
        npass, n = payload.get("pass", 0), payload.get("n", len(checks))
        ok = payload.get("all_pass")
        rows = "".join(
            f'<tr><td>{"✓" if c["ok"] else "✗"}</td>'
            f'<td class=mono>{_e(c["id"])}</td>'
            f'<td>{_e(_trunc(c.get("detail", ""), 110))}</td>'
            f'<td class=diag>{"diag" if c.get("diag") else ""}</td></tr>'
            for c in checks
        )
        cls = "ok" if ok else "bad"
        open_ = "" if ok else " open"
        parts.append(
            f'<details{open_}><summary class="{cls}">{_e(mode)} '
            f'<span class="meta">{npass}/{n}</span></summary>'
            f"<table><tr><th></th><th>check</th><th>detail</th><th></th></tr>{rows}</table></details>"
        )
    return "".join(parts)


def _tabbed(tabs: list[tuple[str, str]]) -> str:
    """A CSS-only (no-JS) tab group from ``[(label, panel_html), …]``. First tab
    is selected. Hidden radios + sibling ``:checked`` selectors switch panels, so
    the report stays self-contained."""
    rules = "".join(
        f"#tab{i}:checked~.tabnav label[for=tab{i}]"
        "{color:var(--ink);background:var(--card);border-color:var(--line)}"
        f"#tab{i}:checked~#panel{i}{{display:block}}"
        for i in range(len(tabs))
    )
    inputs = "".join(
        f'<input class=tabradio type=radio name=rtabs id=tab{i}{" checked" if i == 0 else ""}>'
        for i in range(len(tabs))
    )
    nav = "<nav class=tabnav>" + "".join(
        f'<label class=tablabel for=tab{i}>{_e(lbl)}</label>' for i, (lbl, _) in enumerate(tabs)
    ) + "</nav>"
    panels = "".join(f'<div class=tabpanel id=panel{i}>{h}</div>' for i, (_, h) in enumerate(tabs))
    return f"<style>{rules}</style><div class=tabs>{inputs}{nav}{panels}</div>"


def render_html(
    title: str,
    *,
    report: Any | None = None,
    probes: list | tuple = (),
    verdict: dict | None = None,
    modes: dict | None = None,
    generated_at: str | None = None,
) -> str:
    """Render the single self-contained HTML report string.

    Two tabs when there are probes: **Overview** (the ``verdict`` badge + per-group
    ``modes`` check tables + any Harness ``report`` scenarios) and **Traces** (the
    full per-turn inspect data — the rich ReAct timeline + lobe activation + raw
    JSON for each ``probe``). With no probes it renders the overview alone."""
    probes = list(probes)
    ts = generated_at or datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    overview = [_overview(verdict, modes) if verdict is not None else "", _scorecard(report, probes)]
    if report is not None:
        overview.append("<h2>Scenarios (routing &amp; behavior)</h2>")
        overview.append(_scenarios(report))
    overview_html = "".join(p for p in overview if p)

    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body><div class='wrap'>",
        f"<h1>{_e(title)}</h1><div class='sub'>agent_sdk benchmark · {_e(ts)}</div>",
    ]
    if probes:
        traces_html = "<h2>Probes (real turn internals)</h2>" + "".join(_probe(p) for p in probes)
        parts.append(_tabbed([("Overview", overview_html), (f"Traces ({len(probes)})", traces_html)]))
    else:
        parts.append(overview_html)
    parts.append("</div></body></html>")
    return "".join(parts)


def write_html(
    path: str | Path,
    title: str,
    *,
    report: Any | None = None,
    probes: list | tuple = (),
    verdict: dict | None = None,
    modes: dict | None = None,
    generated_at: str | None = None,
) -> Path:
    """Write the report to ``path`` (creating parent dirs). Returns the path."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_html(title, report=report, probes=probes, verdict=verdict, modes=modes,
                    generated_at=generated_at),
        encoding="utf-8",
    )
    return out


def write_json(path: str | Path, *, report: Any | None = None, probes: list | tuple = ()) -> Path:
    """Companion machine-readable artifact (optional)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": report.summary() if report is not None else None,
        "scenarios": [
            {
                "input": r.scenario.input,
                "expect_path": r.scenario.expect_path,
                "path": list(r.path),
                "activated_lobes": r.activated_lobes,
                "passed": r.passed,
                "failures": r.failures,
            }
            for r in (report.results if report is not None else [])
        ],
        "probes": [p.to_json() for p in probes],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return out
