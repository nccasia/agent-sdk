"""DocGroundingGuard — a written doc must not cite paths that don't exist."""

from __future__ import annotations

from agent_sdk import DocGroundingGuard


def _guard(real_paths, **kw):
    real = set(real_paths)
    return DocGroundingGuard(exists=lambda p: p in real, **kw)


def test_doc_with_real_paths_passes():
    g = _guard({"engine.py", "react/docguard.py"})
    out = g("document", "Write", {"file_path": "ARCHITECTURE.md",
                                   "content": "See engine.py and react/docguard.py."})
    assert out is None
    assert g.events == []


def test_doc_with_phantom_paths_is_refused():
    g = _guard({"react/docguard.py"})
    out = g("document", "Write", {"file_path": "ARCHITECTURE.md",
                                   "content": "The guard lives in guards/docguard.py and memory/recall.py."})
    assert out is not None and "do not exist" in out
    assert "guards/docguard.py" in out and "memory/recall.py" in out
    assert g.events[-1]["action"] == "ungrounded_refs"


def test_refusal_is_bounded_to_avoid_deadlock():
    g = _guard(set(), max_refusals=2)
    inp = {"file_path": "ARCHITECTURE.md", "content": "x.py"}
    assert g("document", "Write", inp) is not None  # 1st steer
    assert g("document", "Write", inp) is not None  # 2nd steer
    assert g("document", "Write", inp) is None      # 3rd: let it through (gate still records)
    assert sum(1 for e in g.events if e["action"] == "ungrounded_refs") == 3


def test_non_doc_writes_and_reads_ignored():
    g = _guard(set())
    # a code write (not a .md doc) is not grounding-checked
    assert g("implement", "Write", {"file_path": "main.py", "content": "import nonexistent.py"}) is None
    # a Read is just recorded, never refused
    assert g("survey", "Read", {"file_path": "phantom.py"}) is None
    assert "phantom.py" in g.read


def test_record_only_measures_without_refusing():
    g = _guard(set(), record_only=True)
    out = g("document", "Write", {"file_path": "DOC.md", "content": "missing.py"})
    assert out is None
    assert g.events and g.events[0]["action"] == "ungrounded_refs"
