"""Synthesize-stage definitions for the OX flow axis.

Each stage is a self-describing ``Stage`` unit (what / when / how + slice +
running model), like the ``Lobe`` authoring pattern; ``.spec`` compiles to the
``FlowStep`` the registry + runner consume (byte-identical at parity).
"""

from __future__ import annotations

from agent_sdk.flows.flow import FlowStep
from agent_sdk.flows.stages.common import Stage


class QnaSynthesize(Stage):
    """The qna answer stage — a direct question answered in one ReAct pass.

    Legacy-parity (ENGINE 0.7.0): an agentic loop over the FULL composed toolset
    (KB retrieval on either backend, skill.read, tasks.*) plus prefetched
    grounding. An empty ``tools`` filter on an agentic step means "all composed
    specs" — a one-shot qna without tools cannot ground KB questions routed here.
    """

    id = "synthesize"
    flow = "qna"
    description = "qna answer: agentic loop, full composed toolset (legacy parity)"
    use_when = "the path is qna — a direct question to answer in one ReAct pass"
    how = "agentic ReAct loop over the full composed toolset; synthesize + recall + skill lobes"
    loop = "agentic"
    lobes = (
        "synthesize",
        "skill_select",
        "skill_active",
        "memory_recall",
        "session_recall",
        "ctxvar_resolve",
        "task_state",
    )


class FallbackSynthesize(Stage):
    """The universal fallback answer stage — the standard flow a turn walks when
    no named path is recognized (emergent), so every turn runs a real flow
    instead of dropping to a flow-less path.

    Same contract as ``qna`` (agentic ReAct loop over the full composed toolset
    + prefetched grounding), so the fallback answers as well as a recognized
    qna turn — the safe, general enterprise-assistant default.
    """

    id = "synthesize"
    flow = "fallback"
    description = "fallback: agentic answer for an unrecognized (emergent) turn"
    use_when = "no named path matched — the general-purpose default answer flow"
    how = "agentic ReAct loop over the full composed toolset; same contract as qna"
    loop = "agentic"
    lobes = (
        "synthesize",
        "skill_select",
        "skill_active",
        "memory_recall",
        "session_recall",
        "ctxvar_resolve",
        "task_state",
    )


class ResearchSynthesize(Stage):
    """Compose the final answer from the research memos (the aggregate stage)."""

    id = "synthesize"
    flow = "research"
    description = "research: compose the answer from research memos"
    use_when = "the research fan-out produced memos that must be composed into an answer"
    how = "single call aggregating the evidence-channel memos (synthesize + research lobes)"
    loop = "single"
    lobes = (
        "synthesize",
        "research",
        "skill_select",
        "skill_active",
        "memory_recall",
        "session_recall",
    )


class ClarifySynthesize(Stage):
    """Re-synthesis in the resolve-referent phase (condense resolved the anaphora).

    Legacy-parity: the resolved follow-up usually needs retrieval — same agentic
    full-toolset contract as qna.
    """

    id = "synthesize"
    flow = "clarify"
    description = "clarify: re-synthesis in the resolve-referent phase (agentic)"
    use_when = "a follow-up whose referent was just resolved needs re-answering"
    how = "agentic ReAct loop after condense resolves the anaphora (synthesize + condense lobes)"
    loop = "agentic"
    lobes = (
        "synthesize",
        "condense",
        "scope_check",
        "memory_recall",
        "session_recall",
    )


class RelationalSynthesize(Stage):
    """Minimal synthesis for greetings / social register (no retrieval)."""

    id = "synthesize"
    flow = "relational"
    description = "relational: minimal synthesis (greeting / social register)"
    use_when = "a greeting or social-register turn that needs no retrieval"
    how = "one call, synthesize lobe only"
    loop = "single"
    lobes = ("synthesize",)


def qna_synthesize() -> FlowStep:
    return QnaSynthesize().spec


def fallback_synthesize() -> FlowStep:
    return FallbackSynthesize().spec


def research_synthesize() -> FlowStep:
    return ResearchSynthesize().spec


def clarify_synthesize() -> FlowStep:
    return ClarifySynthesize().spec


def relational_synthesize() -> FlowStep:
    return RelationalSynthesize().spec
