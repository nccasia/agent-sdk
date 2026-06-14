"""Canonical pinned-lobe contract for the engine.

The output-contract lobes ``filter`` (output safety — :class:`SafetyPlugin`,
default-on) and ``cite`` (citation grounding — :class:`RagPlugin`, opt-in) are
*pinned*: **when present**, the activation network can never deactivate them and
metacognition can never skip them (the ground-or-refuse / safety contract). They
are owned by their plugins, not the kernel — a non-RAG agent simply has no
``cite``, and listing it here is harmless (a pinned id absent from the network is
just not present). Kept here as a single constant so the activation machinery and
the metacognition regulator share one source of truth.

``rag_core.policy.schema.PINNED_LOBES`` is the policy-validation copy; the
two are kept identical by ``tests/test_pinned_lobes_parity.py``.
"""

from __future__ import annotations

PINNED_LOBES: frozenset[str] = frozenset({"cite", "filter"})
"""Output-contract lobe ids that bypass the activation threshold and can never be
disabled *when present* (``filter`` from SafetyPlugin; ``cite`` from RagPlugin)."""
