"""The coding agent's flows — the intent (OY routing) axis.

Each flow is an intent recognized *free + deterministically* from the request (a
declarative ``signal``) that sequences a set of :mod:`coding_agent.flows.stages`:

- ``feature``    — explore → plan → implement → verify → summarize
- ``quick_fix``  — explore → implement → verify → summarize
- ``understand`` — survey → plan → investigate → document (write an architecture doc)
- ``question``   — answer (read/grep/glob the repo, explain; no edits)
"""

from __future__ import annotations

from agent_sdk import flow

from coding_agent.flows.stages import READONLY_STAGES, coding_stages


def coding_flows() -> list:
    # A QUESTION (how/what/why/explain/trace, or anything ending in "?") routes to
    # explore→answer even when long — `not is_question` gates the change flows so a
    # long question never mis-routes to feature. The engine sets `is_question` when
    # the query starts with a wh-word or ends with "?".
    _not_question = {"not": {"flag": "is_question"}}
    return [
        flow(
            "feature", use_when="a multi-step change: a feature, refactor, or new code",
            stages=["explore", "plan", "implement", "verify", "summarize"],
            threshold=0.5, grounds=False,
            signal={"all": [
                _not_question,
                {"any": [
                    {"lexical": ["add", "implement", "create", "build", "feature",
                                 "refactor", "support", "introduce", "rewrite", "migrate"]},
                    {"min_words": 18},
                ]},
            ]},
        ),
        flow(
            "quick_fix", use_when="a small bug fix",
            stages=["explore", "implement", "verify", "summarize"],
            threshold=0.5, grounds=False,
            signal={"all": [
                _not_question,
                {"lexical": ["fix", "bug", "broken", "error", "fails", "failing",
                             "crash", "typo", "incorrect", "regression"]},
            ]},
        ),
        flow(
            "understand", use_when="understand a whole system + write an architecture doc",
            stages=["survey", "plan", "investigate", "document"],
            threshold=0.55, grounds=False,
            signal={"any": [
                {"lexical": ["architecture", "overview", "document the", "introduce the",
                             "map the codebase", "system design", "how the system",
                             "whole codebase", "entire codebase", "the codebase and write"]},
                {"all": [{"lexical": ["understand"]},
                         {"lexical": ["codebase", "system", "architecture", "repo", "project"]}]},
            ]},
        ),
        flow(
            "question", use_when="a question about the code (no change)",
            stages=["answer"], threshold=0.4, grounds=False,
            signal={"any": [
                {"flag": "is_question"},
                {"lexical": ["how", "what", "why", "explain", "trace", "describe",
                             "where", "which", "does", "summarize"]},
                {"const": 0.3},
            ]},
        ),
    ]


__all__ = ["coding_flows", "coding_stages", "READONLY_STAGES"]
