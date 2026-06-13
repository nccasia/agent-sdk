"""The flat sparse weight surface — the engine-wide optimization seam (G5).

One flat dict (cf. DEFAULT_NODE_WEIGHTS): priors, signal weights, edges,
thresholds, path biases, layer prompt budgets. Sparse per-bot overrides
arrive via ``policy.flow_lobe_weights`` through ``merge_lobe_weights``; the
``/flow-improve`` waves and the attentionbench ``tuning`` mode operate here.
"""

from __future__ import annotations

from agent_sdk.lobes.network import default_lobes, default_paths


def _build_default_weights() -> dict[str, float]:
    """One flat dict (cf. DEFAULT_NODE_WEIGHTS): priors, signal weights,
    edges, thresholds, path biases, layer prompt budgets. Sparse per-bot
    overrides arrive via ``policy.flow_lobe_weights`` through
    ``merge_lobe_weights`` — the engine-wide optimization surface (G5)."""
    weights: dict[str, float] = {
        # per-signal weights (w_<signal>) — global across lobes
        "w_memory_enabled": 1.0,
        # scoped-context levers (conversation > channel > user > bot) —
        # informational at parity; raise per-bot to bias recall by scope.
        "w_mem_conversation": 0.0,
        "w_mem_channel": 0.0,
        "w_mem_user": 0.0,
        "w_mem_bot": 0.0,
        "w_skills_declared": 1.0,
        "w_anaphora": 0.6,
        "w_short_query": 0.6,
        "w_has_history": 0.0,  # informational at parity; a tuning lever
        "w_scope_gate": 1.0,
        "w_has_stage_classify": 1.0,
        # classify-skip: set 0.0 in adaptive mode to always pay for the router.
        "w_simple_shape": -0.6,
        "w_route_complex": 1.0,
        "w_tools_used": 0.0,  # informational — cite is pinned regardless
        "w_fixed_format": 1.0,
        # layered prompt-segment budgets (tokens) — generalizes
        # context_budget_tokens into per-layer knobs (G4)
        "budget_memory": 800,
        "budget_skill": 400,
        "budget_cognition": 600,
    }
    for lobe in default_lobes():
        weights[f"prior_{lobe.id}"] = lobe.prior
        weights[f"min_{lobe.id}"] = lobe.min_activation
        weights[f"budget_{lobe.id}"] = float(lobe.attends.budget_tokens)
        for dst, w in lobe.edges.items():
            weights[f"edge_{lobe.id}__{dst}"] = w
    for path in default_paths():
        for member in path.members:
            weights[f"path_{path.name}__{member}"] = path.bias.get(member, 0.0)
    return weights


DEFAULT_LOBE_WEIGHTS: dict[str, float] = _build_default_weights()
