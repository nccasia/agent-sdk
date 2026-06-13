"""Deterministic tests for coding-agent-bench's own scoring logic — the accuracy
gates (path-existence + subsystem coverage) and the baseline ratchet. Keeps the
measurement instrument itself regression-safe (free, no provider)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("cab_run", Path(__file__).with_name("run.py"))
run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run)


def test_accuracy_metrics_flags_phantom_paths(tmp_path):
    (tmp_path / "engine.py").write_text("x=1")
    (tmp_path / "react").mkdir()
    (tmp_path / "react" / "docguard.py").write_text("x=1")
    doc = "The engine is in engine.py; the guard in react/docguard.py and guards/old.py."
    m = run.accuracy_metrics(doc, str(tmp_path))
    assert m["ref_count"] == 3
    assert m["missing_paths"] == ["guards/old.py"]
    assert m["path_exist_ratio"] == round(2 / 3, 3)


def test_anchors_are_real_subsystems(tmp_path):
    (tmp_path / "engine.py").write_text("x=1")
    (tmp_path / "__init__.py").write_text("")
    (tmp_path / "lobes").mkdir()
    (tmp_path / "clients").mkdir()
    anchors = run._anchors(str(tmp_path))
    assert "lobes" in anchors and "clients" in anchors and "engine" in anchors
    assert "__init__" not in anchors
    m = run.accuracy_metrics("It has lobes and clients and an engine.", str(tmp_path))
    assert m["anchor_coverage"] == 1.0


def test_ratchet_detects_regression(tmp_path, monkeypatch):
    base = {"metrics": {"hops": 40, "input_tokens": 400_000, "redundant_writes": 0,
                        "path_exist_ratio": 1.0, "anchor_coverage": 0.9}}
    bf = tmp_path / "baseline.json"
    bf.write_text(__import__("json").dumps(base))
    monkeypatch.setattr(run, "BASELINE", bf)
    # within tolerance → ok
    g = run.ratchet({"hops": 44, "input_tokens": 420_000, "redundant_writes": 0,
                     "path_exist_ratio": 1.0, "anchor_coverage": 0.9})
    assert g["ok"]
    # hops blow up + accuracy drops → regression
    g2 = run.ratchet({"hops": 120, "input_tokens": 1_600_000, "redundant_writes": 5,
                      "path_exist_ratio": 0.5, "anchor_coverage": 0.5})
    assert not g2["ok"]
    assert "hops" in g2["detail"] and "redundant_writes" in g2["detail"]


def test_ratchet_absent_baseline_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "BASELINE", tmp_path / "nope.json")
    assert run.ratchet({"hops": 9999}) is None
