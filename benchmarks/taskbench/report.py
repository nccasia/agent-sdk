"""taskbench's own HTML report — task goal, the todo rail, the SQL the agent ran,
the submitted answer vs ground truth, duration, and the execution process. Self-contained.
"""

from __future__ import annotations

import html
from pathlib import Path

_STC = {"done": "#1F6B4A", "skipped": "#6b7280", "todo": "#B8845B",
        "doing": "#2563eb", "blocked": "#b91c1c"}

_CSS = """
:root{--ink:#0e0e0c;--paper:#fafaf7;--line:#e5e3dc;--mut:#6b6b63}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);
font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;padding:24px;max-width:1080px;margin:auto}
h1{font-size:20px;margin:0 0 4px}h2{font-size:14px;margin:14px 0 6px;color:#46443d}
.mut{color:var(--mut)}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.matrix{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.chip{padding:3px 9px;border-radius:999px;font-size:12px;font-weight:600}
.ready{background:#dcf0e6;color:#1f6b4a}.notready{background:#fbe2e2;color:#b91c1c}.unmeas{background:#eee9df;color:#8a6d3b}
.card{border:1px solid var(--line);border-radius:10px;margin:14px 0;overflow:hidden;background:#fff}
.card>header{display:flex;gap:9px;align-items:center;flex-wrap:wrap;padding:11px 14px;background:#f3f1ea;border-bottom:1px solid var(--line)}
.badge{font-size:11px;padding:2px 7px;border-radius:6px;background:#e5e3dc;color:#46443d;font-weight:600}
.body{padding:12px 14px}.q{font-size:14px;margin:0 0 10px}
table{border-collapse:collapse;width:100%;font-size:13px;margin:2px 0 10px}
th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--mut);font-weight:600}
.st{font-weight:700;font-size:10.5px;text-transform:uppercase;letter-spacing:.03em}
.ok{color:#1f6b4a}.bad{color:#b91c1c}
.ans{background:#f7f6f1;border-left:3px solid #1F6B4A;padding:8px 11px;border-radius:6px;white-space:pre-wrap}
.ans.wrong{border-left-color:#b91c1c}
.proc{list-style:none;padding:0;margin:0;font-size:12.5px}.proc li{padding:3px 0;border-bottom:1px dotted var(--line)}
.sql{background:#f3f1ea;padding:1px 5px;border-radius:4px}
.checks span{display:inline-block;margin:2px 8px 2px 0;font-size:12px}.pass{border-left:3px solid #1f6b4a;padding-left:5px}.fail{border-left:3px solid #b91c1c;padding-left:5px}
"""


def _esc(x) -> str:
    return html.escape(str(x))


def _proc_line(tc: dict) -> str:
    """Render one ordered tool call (name + input + output) from the trace."""
    name = tc.get("name", "")
    inp = tc.get("input") or {}
    out = str(tc.get("output", ""))
    if name == "db.schema":
        return "<b>db.schema</b>"
    if name == "db.query":
        err = out.startswith("Error")
        tail = (f'<span class="bad">→ {_esc(out[:80])}</span>' if err
                else f'<span class="mut">→ {_esc(out.splitlines()[0][:60]) if out else "ok"}</span>')
        return f'<b>db.query</b> <span class="sql">{_esc(inp.get("sql"))}</span> {tail}'
    if name == "todos":
        act = inp.get("action", "")
        extra = inp.get("title") or inp.get("result") or inp.get("question") or ""
        return f'<b>todos·{_esc(act)}</b> {_esc(extra)}'
    if name == "submit":
        return f'<b>submit</b> ✅ <span class="mut">{_esc((inp.get("answer") or "")[:80])}</span>'
    return f"<b>{_esc(name)}</b> {_esc(str(inp)[:60])}"


def _case_card(r: dict) -> str:
    task = r["task"]
    ans_ok = next((c["ok"] for c in r["checks"] if c["id"] == "answer_correct"), False)
    facts = ", ".join(_esc(f) for f in r.get("facts", []))
    proc = "".join(f"<li>{_proc_line(tc)}</li>" for tc in r.get("tool_calls", [])) or "<li class=mut>no tool calls</li>"
    checks = "".join(
        f'<span class="{"pass" if c["ok"] else "fail"}">{"✓" if c["ok"] else "✗"} {_esc(c["id"])}'
        f'{"" if c["ok"] else " — " + _esc(c["detail"])}{" (diag)" if c.get("diag") else ""}</span>'
        for c in r["checks"])
    ok = all(c["ok"] for c in r["gating"])
    return f"""
<div class="card">
  <header>
    <span class="chip {'ready' if ok else 'notready'}">{'PASS' if ok else 'FAIL'}</span>
    <b class="mono">{_esc(task['id'])}</b>
    <span class="badge">cap{task['capability']} · {_esc(r['cap_name'])}</span>
    <span class="badge">flow: {_esc(r['flow'])}</span>
    <span class="mut" style="margin-left:auto">{r['duration_ms']} ms · {r['tok_in']}/{r['tok_out']} tok · {len(r.get('queries', []))} queries</span>
  </header>
  <div class="body">
    <p class="q">❓ <b>{_esc(task.get('question'))}</b></p>
    <h2>Answer {'✓ correct' if ans_ok else '✗ incorrect'}</h2>
    <div class="ans {'' if ans_ok else 'wrong'}">{_esc(r.get('answer') or '(no answer)')}</div>
    <div class="mut" style="margin:6px 0 0">ground truth: <span class="mono">{facts}</span></div>
    <h2>Execution process (plan → per-todo SQL → deliver)</h2>
    <ol class="proc">{proc}</ol>
    <h2>Checks</h2>
    <div class="checks">{checks}</div>
  </div>
</div>"""


def write_task_report(path, *, results, measured, unmeasured, verdict, label) -> Path:
    matrix = "".join(
        f'<span class="chip {"ready" if p["all_pass"] else "notready"}">'
        f'{"READY" if p["all_pass"] else "NOT_READY"} · {_esc(lbl)}</span>'
        for lbl, p in sorted(measured.items()))
    matrix += "".join(f'<span class="chip unmeas" title="{_esc(rsn)}">UNMEASURED · {_esc(lbl)}</span>'
                      for lbl, rsn in sorted(unmeasured.items()))
    cards = "".join(_case_card(r) for r in results)
    ready = sum(1 for p in measured.values() if p["all_pass"])
    solved = sum(1 for r in results if r["metrics"]["correct"])
    doc = f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>taskbench · {_esc(label)}</title><style>{_CSS}</style></head><body>
<h1>taskbench — plan &amp; drive realistic tasks (live, real data)</h1>
<div class="mut">{_esc(label)} · {solved}/{len(results)} solved · {ready}/{len(measured)} capabilities READY · verdict <b>{_esc(verdict)}</b></div>
<div class="matrix">{matrix}</div>
{cards}
</body></html>"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc, encoding="utf-8")
    return p
