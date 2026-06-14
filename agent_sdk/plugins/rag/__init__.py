"""RAG / grounding capability plugin — the citation contract, out of the kernel.

Owns everything retrieval-augmented-QA needs and a general agent does not: the
``cite`` (citation grounding) output-contract lobe plus the citation *logic* via
two engine seams —

- ``add_finalize_hook`` — on every turn, extract ``[chunk_id]`` citations from the
  answer against the evidence channel, backfill paraphrased uses, strip the inline
  markers from the user-facing text, and enforce **ground-or-refuse** (a grounding
  flow that requires citations but found none refuses).
- ``add_tool_result_hook`` — pull citations a tool emits as ``{"citations": [...]}``.

**Opt-in (default-off).** Most agents (coding / chat / task) have no retrieval, so
this plugin is not in the default capability set — plug it in (``plugins=[RagPlugin()]``)
or set ``require_citations=True`` (which auto-enables it). With it absent the engine
carries NO citation logic and the agent simply does not ground. Output *safety*
(the ``filter`` lobe) is a separate, default-on concern (:class:`SafetyPlugin`) —
a non-RAG agent still gets safety, just not grounding. ``cite`` is pinned *within
this plugin* (the activation network can't deactivate it on a grounding path), but
it is not a core pin.
"""

from __future__ import annotations

from agent_sdk.plugins.base import AgentSetup
from agent_sdk.plugins.rag.citation import (
    backfill_citations,
    citations_from_text,
    extract_tool_citations,
    strip_citation_markers,
)
from agent_sdk.plugins.rag.lobes import cite as _cite

__all__ = ["RagPlugin"]


def _finalize_grounding(answer, citations, chunks, grounds, require_citations):
    """Own the grounding contract: extract + backfill + strip + ground-or-refuse.

    Returns ``(answer, citations, refusal_reason | None)``. ``citations`` coming in
    already holds any tool-emitted ones; we add the text-marker + paraphrase-overlap
    citations, then strip the markers from the prose. A grounding flow requiring
    citations but ending with none refuses (``no_citations``)."""
    out = list(citations)
    seen = {c.chunk_id for c in out}
    for c in citations_from_text(answer, chunks):
        if c.chunk_id not in seen:
            seen.add(c.chunk_id)
            out.append(c)
    out.extend(backfill_citations(answer, chunks, out))
    clean = strip_citation_markers(answer)
    refusal = "no_citations" if (require_citations and grounds and not out) else None
    return clean, out, refusal


def _tool_result_citations(tool_name, output):
    return extract_tool_citations(output)


class RagPlugin:
    """Retrieval-augmented grounding: cite/filter lobes + the citation contract."""

    name = "rag"

    def lobes(self) -> list:
        return [_cite.LOBE]

    def install(self, setup: AgentSetup) -> None:
        for lb in self.lobes():
            setup.add_lobe(lb)
        setup.add_finalize_hook(_finalize_grounding)
        setup.add_tool_result_hook(_tool_result_citations)
