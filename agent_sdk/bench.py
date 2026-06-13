"""A thin benchmark harness over scenarios + trace/route assertions.

Wraps the no-LLM ``inspect`` snapshot (routing) and optional full ``query`` runs
into one public surface — the attentionbench/flowbench/skillbench trace-reading
patterns as a small library.

    report = await Harness(agent).run([
        Scenario(input="compare A and B", expect_path="research"),
        Scenario(input="hello", expect_path="qna"),
    ])
    report.summary()   # path_accuracy, lobe_recall, ...
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Scenario", "ScenarioResult", "Report", "Harness"]


@dataclass
class Scenario:
    input: str
    expect_path: str | None = None
    expect_lobes: list[str] | None = None  # lobes that must be activated
    expect_flow: list[str] | None = None  # resolved stage ids
    run_llm: bool = False  # also run a full query() (needs a real/fake client)
    expect_status: str | None = None


@dataclass
class ScenarioResult:
    scenario: Scenario
    path: tuple[str, float]
    activated_lobes: list[str]
    flow: list[str]
    passed: bool
    failures: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    status: str | None = None


@dataclass
class Report:
    results: list[ScenarioResult]

    def summary(self) -> dict:
        n = len(self.results) or 1
        path_hits = sum(
            1
            for r in self.results
            if r.scenario.expect_path is None or r.path[0] == r.scenario.expect_path
        )
        # lobe recall: fraction of expected lobes that activated, averaged over scenarios that assert lobes
        recalls = []
        for r in self.results:
            if r.scenario.expect_lobes:
                want = set(r.scenario.expect_lobes)
                got = set(r.activated_lobes)
                recalls.append(len(want & got) / len(want))
        passed = sum(1 for r in self.results if r.passed)
        lat = sorted(r.latency_ms for r in self.results)
        p95 = lat[int(0.95 * (len(lat) - 1))] if lat else 0.0
        return {
            "scenarios": len(self.results),
            "passed": passed,
            "pass_rate": round(passed / n, 4),
            "path_accuracy": round(path_hits / n, 4),
            "lobe_recall": round(sum(recalls) / len(recalls), 4) if recalls else None,
            "p95_latency_ms": round(p95, 2),
        }


class Harness:
    def __init__(self, agent: Any):
        self.agent = agent

    async def run(self, scenarios: list[Scenario]) -> Report:
        results: list[ScenarioResult] = []
        for sc in scenarios:
            t0 = time.time()
            snap = self.agent.inspect(sc.input)
            activated = [lb["id"] for lb in snap.lobes if lb.get("activated")]
            failures: list[str] = []
            status = None
            if sc.expect_path is not None and snap.path[0] != sc.expect_path:
                failures.append(f"path {snap.path[0]!r} != expected {sc.expect_path!r}")
            if sc.expect_lobes:
                missing = set(sc.expect_lobes) - set(activated)
                if missing:
                    failures.append(f"lobes not activated: {sorted(missing)}")
            if sc.expect_flow is not None and snap.flow != sc.expect_flow:
                failures.append(f"flow {snap.flow} != expected {sc.expect_flow}")
            if sc.run_llm:
                result = await self.agent.query(sc.input)
                status = result.status
                if sc.expect_status and status != sc.expect_status:
                    failures.append(f"status {status!r} != expected {sc.expect_status!r}")
            results.append(
                ScenarioResult(
                    scenario=sc,
                    path=tuple(snap.path),
                    activated_lobes=activated,
                    flow=list(snap.flow),
                    passed=not failures,
                    failures=failures,
                    latency_ms=(time.time() - t0) * 1000,
                    status=status,
                )
            )
        return Report(results)
