"""Rich visual viewer — reuse the polished benchmark ``viewer.html`` for the SDK.

The project's ``benchmarks/_shared/viewer.html`` is a self-contained, dependency-
free interactive report (conversation/turn selector · timeline · pipeline (OX) ·
context lobes (OY) · reasoning · tools · hotspots · raw JSON, with drag-drop
fallback). It is an HTML asset with no Python coupling, so the SDK reuses it
directly: :func:`to_viewer_record` maps an SDK :class:`~agent_sdk.probe.ProbeRecord`
into the record schema the viewer reads, and :func:`render_viewer_html` injects
the records at the template's ``<!--TRACE_DATA-->`` seam.

    from agent_sdk.probe import probe
    from agent_sdk.viewer import write_viewer

    recs = [await probe(agent, "add a multiply function", label="turn 1")]
    write_viewer("results/report.html", recs, label="coding-agent-bench")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_sdk.probe import ProbeRecord

__all__ = ["to_viewer_record", "render_viewer_html", "write_viewer"]

_VIEWER = Path(__file__).parent / "assets" / "viewer.html"


def _flow_steps(p: ProbeRecord) -> list[dict]:
    """Map the SDK's per-stage trace into the viewer's ``flow_steps`` shape."""
    out: list[dict] = []
    for s in p.stages:
        stage = s.get("stage")
        calls = [c for c in p.llm_calls if c.get("stage") == stage]
        tok_in = sum((c.get("usage") or {}).get("input_tokens", 0) for c in calls)
        tok_out = sum((c.get("usage") or {}).get("output_tokens", 0) for c in calls)
        tools = sorted({
            st.get("name") for st in s.get("steps", []) if st.get("kind") == "tool_use"
        } - {None})
        # Prefer the engine's real per-stage telemetry (hops, input_tokens,
        # funnel_obs_chars); fall back to the synthesized stub for old traces.
        meta = dict(s.get("metadata") or {})
        meta.setdefault("hops", len(calls))
        meta["skipped"] = bool(s.get("skipped"))
        out.append({
            "step": stage,
            "flow": s.get("flow"),
            "loop": s.get("loop", "single"),
            "lobes": s.get("lobes", []),
            "tools": tools,
            "tokens_in": tok_in,
            "tokens_out": tok_out,
            "tokens_after": tok_out,
            "latency_ms": 0,
            "node_count": len(s.get("lobes", [])),
            "system_prompt": s.get("system_prompt", ""),  # composed prompt (Prompt panel)
            "system_segments": s.get("system_segments", []),  # provenance: colour by lobe/section
            "metadata": meta,
            "funnel_obs_chars": meta.get("funnel_obs_chars", []),
            "attention": s.get("attention", {"nodes": [], "tiers": []}),
        })
    return out


def _context_funnel(p: ProbeRecord) -> dict:
    """Per-stage context-cost series for the viewer's Context/Funnel panel."""
    stages = (p.attention or {}).get("stages")
    if not stages:
        stages = [
            {
                "stage": s.get("stage"),
                "input_tokens": (s.get("metadata") or {}).get("input_tokens", 0),
                "funnel_obs_chars": (s.get("metadata") or {}).get("funnel_obs_chars", []),
                "tier_counts": (s.get("attention") or {}).get("tier_counts", {}),
            }
            for s in p.stages
        ]
    return {"stages": stages, "tier_counts": (p.attention or {}).get("tier_counts", {})}


def to_viewer_record(p: ProbeRecord) -> dict:
    """Adapt one ``ProbeRecord`` to the viewer's record schema.

    ``trace.lobes`` and ``trace.llm_calls`` already match the viewer's shapes
    (both come straight from the engine's activation + ReAct capture), so the
    timeline / context-lobes / reasoning / tools panels render from real data.
    """
    trace = {
        "path": p.path or {"name": p.flow, "score": p.flow_score},
        "flow": {"name": p.flow},
        "lobes": p.lobes,
        "flow_steps": _flow_steps(p),
        "llm_calls": p.llm_calls,
        "usage_rollup": {
            "input_tokens": (p.usage or {}).get("input_tokens", 0),
            "output_tokens": (p.usage or {}).get("output_tokens", 0),
        },
        "meta": (p.meta_actions[-1] if p.meta_actions else {}),
        "attention": p.attention or {"nodes": [], "tiers": []},
        "context_funnel": _context_funnel(p),
        "skills": [],
    }
    # Optional task surface for the Tasks panel: a bench/agent may attach
    # ``task_items`` (active checklists) / ``task_templates`` to the record so the
    # Tasks tab renders the live todo rail. Absent ⇒ the panel shows its empty
    # state exactly as before (purely additive).
    trace["scratchpad"] = getattr(p, "task_scratchpad", {}) or {}
    return {
        "label": p.label,
        "query": p.query,
        "answer": p.answer,
        "status": p.status,
        "trace": trace,
        "tool_calls": p.tool_calls,
        "context": {
            "task_items": list(getattr(p, "task_items", []) or []),
            "task_templates": list(getattr(p, "task_templates", []) or []),
        },
        "hints": p.hints,
        "error": p.error,
    }


def render_viewer_html(
    records: list[ProbeRecord],
    *,
    label: str = "",
    verdict: dict | None = None,
    modes: dict | None = None,
) -> str:
    """Inject the probe records into the viewer template (one self-contained HTML).

    ``verdict`` (``{status, reasons, metrics}``) + ``modes`` (``{group: {checks,
    n, pass, all_pass}}``), when given, render a benchmark OVERVIEW banner above
    the per-turn panels — so one file carries the readiness verdict AND the full
    per-turn trace detail. A plain trace dump omits both and shows traces only."""
    template = _VIEWER.read_text(encoding="utf-8")
    data: dict[str, Any] = {"label": label, "records": [to_viewer_record(r) for r in records]}
    if verdict is not None:
        data["verdict"] = verdict
    if modes is not None:
        data["modes"] = modes
    payload = json.dumps(data, ensure_ascii=False, default=str)
    payload = payload.replace("</", "<\\/")  # never close the embedded block early
    block = f'<script id="trace-data" type="application/json">{payload}</script>'
    return template.replace("<!--TRACE_DATA-->", block)


def write_viewer(
    path: str | Path,
    records: list[ProbeRecord],
    *,
    label: str = "",
    verdict: dict | None = None,
    modes: dict | None = None,
) -> Path:
    """Write the rich viewer HTML to ``path`` (creating parent dirs). Returns it."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_viewer_html(records, label=label, verdict=verdict, modes=modes),
        encoding="utf-8",
    )
    return out


def _as_records(items: Any) -> list[ProbeRecord]:
    return [i for i in items if isinstance(i, ProbeRecord)]
