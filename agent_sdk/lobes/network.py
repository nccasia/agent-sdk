"""The default network — explicit, ordered aggregation of every lobe and path.

THIS is the single place execution order lives: declaration order below =
intra-layer `order` = the forward-DAG rank edges validate against. A new
default lobe/path is one import + one list entry (or, per-bot, a registry row
via ``LobeRegistry.add_row`` — never an interpreter branch).

The DEFAULT registry is the DEGENERATE NETWORK: at default weights it
reproduces the legacy decision table exactly (the migration anchor — recall
lobes always-on like today's unconditional one-pass loads, the
classify→plan→research chain at edge weight 1.0, thresholds equal to the
legacy ``_should_*`` predicates). The parity fixture matrix in
``tests/test_lobe_network.py`` is the proof obligation.
"""

from __future__ import annotations

from agent_sdk.flows.compat import Stage
from agent_sdk.lobes import cognition, expression, memory, paths, skill, tools
from agent_sdk.lobes.runtime import BaseLobe
from agent_sdk.network.activation import LobeSpec, PathSpec


def _core_lobe_objects() -> list[BaseLobe]:
    """The core network — the lobes intrinsic to *every* PreAct agent, owned by ``lobes/``:
    memory recall, skills, adaptive tool selection, the cognition reasoning spine, and the
    reply flow (``respond``). These are not toggleable.

    Output styling (``format``), grounding (``cite``/``filter``), and task execution are
    *toggleable* plugin capabilities — ``FormatPlugin`` / ``SafetyPlugin`` (default-on) and the
    opt-in ``TaskPlugin`` — not core. See ``default_lobe_objects``."""
    return [
        # B2 Memory — recall.
        memory.memory_recall.LOBE,
        memory.session_recall.LOBE,
        memory.ctxvar_resolve.LOBE,
        # B3 Skill — progressive-disclosure skills.
        skill.skill_select.LOBE,
        skill.skill_active.LOBE,
        # B3 Tools — adaptive tool-exposure selection.
        tools.tool_select.LOBE,
        # B4 Cognition — the work.
        cognition.condense.LOBE,
        cognition.scope_check.LOBE,
        cognition.classify.LOBE,
        cognition.plan.LOBE,
        cognition.research.LOBE,
        cognition.synthesize.LOBE,
        # B5 Expression — the reply flow.
        expression.respond.LOBE,
    ]


def default_lobe_objects() -> list[BaseLobe]:
    """The full default network = the core lobes + the default-on extension lobes (``cite`` /
    ``filter`` grounding from ``SafetyPlugin``, ``format`` styling from ``FormatPlugin``), woven
    into canonical ``(layer, order)`` order. The engine re-sorts to this order regardless of
    contribution order, so an extension lobe lands in its canonical DAG position."""
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
    parity fixture matrix is the proof. Paths bias, never gate."""
    return [
        paths.qna.PATH,
        paths.research.PATH,
        paths.clarify.PATH,
        paths.relational.PATH,
        # Steward mode — recognizer keys solely on the harness-set
        # config_mode flag, so normal turns can never resolve here.
        paths.onboarding.PATH,
    ]


def default_stages() -> list[Stage]:
    """Deprecated stage adapter; the source of truth is ``default_flows()``."""
    from agent_sdk.flows.compat import default_stages as _default_stages

    return _default_stages()
