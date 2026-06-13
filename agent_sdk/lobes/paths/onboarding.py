"""onboarding — the self-configuration (steward) mode's path.

Score: fully deterministic, keyed on ONE harness-set signal — ``config_mode``
(the worker's activation helper flips it only for a Builder+ sender whose
conversation is flagged via the exact ``@bot onboarding`` command; see
``worker_retrieve.cli._resolve_config_mode``). The LLM never influences this
signal, so the path is unreachable in normal mode — that property is what
keeps the degenerate-parity matrix green and the admin toolset invisible to
ordinary turns (strict mode isolation).

Excluder (→ 0.0): fired prompt — a scheduled task firing into a flagged
conversation must still run task_execute, never the steward flow.

Members/bias: skill_activate +0.2 (the steward skill IS the persona),
task_state +0.1 (tasks stay manageable inside the mode).
Gates: degenerate-parity matrix (recognizer must be 0.0 without the flag) +
the mode-activation table test in worker-retrieve.
"""

from __future__ import annotations

from agent_sdk.lobes.patterns import FIRED_PROMPT_RE
from agent_sdk.network.activation import PathSpec


def recognize(ctx: dict) -> float:
    if not ctx.get("config_mode"):
        return 0.0
    if FIRED_PROMPT_RE.search(str(ctx.get("query") or "")):
        return 0.0
    return 1.0


PATH = PathSpec(
    name="onboarding",
    recognizer=recognize,
    members=("skill_select", "skill_active", "task_state"),
    bias={"skill_select": 0.2, "skill_active": 0.2, "task_state": 0.1},
    # Steward mode configures the bot via admin.* tools and never retrieves a
    # KB — nothing to cite, so the cite/filter output-contract lobes stay dark
    # (they were already dormant: the onboarding flow runs no cite/filter step).
    # Grounding enforcement is unaffected — it lives in the interpreter
    # (enforce_citations), gated on whether retrieval actually ran.
    grounds=False,
)
