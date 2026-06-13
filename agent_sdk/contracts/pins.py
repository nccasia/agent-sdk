"""Canonical pinned-lobe contract for the engine.

The two output-contract lobes ``cite`` and ``filter`` are *pinned*: the
activation network can never deactivate them (the PRD ground-or-refuse
invariant). This is the engine's one structural lobe-name commitment — kept
here in the SDK as a single constant so the activation machinery and the
metacognition regulator share one source of truth without importing the
project's policy schema.

``rag_core.policy.schema.PINNED_LOBES`` is the policy-validation copy; the
two are kept identical by ``tests/test_pinned_lobes_parity.py``.
"""

from __future__ import annotations

PINNED_LOBES: frozenset[str] = frozenset({"cite", "filter"})
"""Lobe ids that bypass the activation threshold and can never be disabled."""
