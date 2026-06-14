"""The default network — domain-driven aggregation of every lobe and path.

Each domain MODULE owns its lobes (``<domain>.LOBES``) and the ``paths`` domain
owns ``PATHS``; this module just concatenates them across domains, in B-layer
order, and the engine re-sorts by ``(layer, order)`` — so a new default lobe is
one entry in its domain's ``LOBES`` (or, per-bot, a registry row via
``LobeRegistry.add_row``), never an edit to a central list and never an
interpreter branch.

The DEFAULT registry is the DEGENERATE NETWORK: at default weights it
reproduces the legacy decision table exactly (the migration anchor — recall
lobes always-on like today's unconditional one-pass loads, the
classify→plan→research chain at edge weight 1.0, thresholds equal to the
legacy ``_should_*`` predicates). The parity fixture matrix in
``tests/test_lobe_network.py`` is the proof obligation.
"""

from __future__ import annotations

from agent_sdk import paths
from agent_sdk.cognition import lobes as cognition_lobes
from agent_sdk.expression import lobes as expression_lobes
from agent_sdk.flows.compat import Stage
from agent_sdk.lobes.runtime import BaseLobe
from agent_sdk.memory import lobes as memory_lobes
from agent_sdk.network.activation import LobeSpec, PathSpec
from agent_sdk.skills import lobes as skill_lobes
from agent_sdk.tools import lobes as tools_lobes

# The core domains, in B-layer order. Each domain package owns its ``LOBES`` in a
# ``lobes`` subpackage (``agent_sdk.<domain>.lobes``); this network is their
# concatenation (then ``(layer, order)``-sorted in ``default_lobe_objects``).
_CORE_LOBE_DOMAINS = (
    memory_lobes,  # B2 Memory — recall (agent_sdk.memory.lobes)
    skill_lobes,  # B3 Skill — progressive-disclosure skills (agent_sdk.skills.lobes)
    tools_lobes,  # B3 Tools — adaptive tool-exposure selection (agent_sdk.tools.lobes)
    cognition_lobes,  # B4 Cognition — the reasoning spine (agent_sdk.cognition.lobes)
    expression_lobes,  # B5 Expression — the reply flow (agent_sdk.expression.lobes)
)


def _core_lobe_objects() -> list[BaseLobe]:
    """The core network — the lobes intrinsic to *every* PreAct agent, gathered from
    each domain's ``LOBES``: memory recall, skills, adaptive tool selection, the
    cognition reasoning spine, and the reply flow (``respond``). Not toggleable.

    Output styling (``format``), output safety (``filter``), citation grounding (``cite``), and
    task execution are *toggleable* plugin capabilities — ``FormatPlugin`` / ``SafetyPlugin``
    (default-on) and the opt-in ``RagPlugin`` (``cite``) / ``TaskPlugin`` — not core. See
    ``default_lobe_objects``."""
    objs: list[BaseLobe] = []
    for domain in _CORE_LOBE_DOMAINS:
        objs.extend(domain.LOBES)
    return objs


def default_lobe_objects() -> list[BaseLobe]:
    """The full default network = the core lobes + the default-on extension lobes (``filter``
    output safety from ``SafetyPlugin``, ``format`` styling from ``FormatPlugin``), woven into
    canonical ``(layer, order)`` order. Citation grounding (``cite``) is NOT here — it is opt-in
    via ``RagPlugin``. The engine re-sorts to this order regardless of contribution order, so an
    extension lobe lands in its canonical DAG position."""
    # Lazy import: the extension plugins import low-level lobe primitives only — no cycle.
    from agent_sdk.plugins import capability_lobes

    objs = _core_lobe_objects() + capability_lobes()
    objs.sort(key=lambda lb: (lb.spec.layer, lb.spec.order))
    return objs


def default_lobes() -> list[LobeSpec]:
    """The legacy flow, transformed into lobe rows. Order within a layer is
    execution order (and the forward-DAG rank edges validate against)."""
    return [lobe.spec for lobe in default_lobe_objects()]


def default_paths() -> list[PathSpec]:
    """Path biases are deliberately small (≤0.3): a bias may tip a borderline
    member, never cross a parity threshold gap on its own — the degenerate-
    parity fixture matrix is the proof. Paths bias, never gate. The ``paths``
    domain owns the list (``paths.PATHS``); a new path is one entry there."""
    return list(paths.PATHS)


def default_stages() -> list[Stage]:
    """Deprecated stage adapter; the source of truth is ``default_flows()``."""
    from agent_sdk.flows.compat import default_stages as _default_stages

    return _default_stages()
