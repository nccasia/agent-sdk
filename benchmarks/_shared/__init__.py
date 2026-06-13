"""Reusable SDK-native benchmark helpers (verdict, report, embedding for judging).

Built purely on the agent-sdk public surface; no project (rag_core/agent_core) import, so the
SDK leaf stays standalone. The benches are LIVE (real provider) — they build the real
``PreactAgent`` themselves; the only shared pieces are the verdict composition, the HTML report
writer, and the concept embedding used to judge recall. (The old FakeClient probe *vehicles* and
scripted *runner* lived here for stub benches — those were integration-test material and have been
removed; stub-driven coverage lives in ``tests/`` and the example test suites.)
"""

from benchmarks._shared.embed import CONCEPTS, concept_embed, concept_of
from benchmarks._shared.provider import load_provider
from benchmarks._shared.report import render_consolidated, write_consolidated
from benchmarks._shared.verdict import compose_verdict

__all__ = [
    "compose_verdict",
    "render_consolidated",
    "write_consolidated",
    "load_provider",
    "concept_embed",
    "concept_of",
    "CONCEPTS",
]
