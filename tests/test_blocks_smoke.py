"""Smoke test — the ported deterministic building blocks resolve + run.

This locks in that the mechanical port of ``agent_core.sdk`` → ``agent_sdk``
preserved the pure activation core (the conformance surface). The new PreAct
façade is tested in its own modules.
"""

from __future__ import annotations

from agent_sdk import (
    Blackboard,
    ContextNode,
    LobeRegistry,
    LobeSpec,
    PathSpec,
    build_attention,
    propagate,
    recognize_paths,
    resolve_path,
    validate_network,
)
from agent_sdk.flows.flow import Flow, FlowStep
from agent_sdk.network.activation import (
    LAYER_COGNITION,
    LAYER_EXPRESSION,
    merge_lobe_weights,
)


def _spec(id_: str, layer: int, **kw) -> LobeSpec:
    return LobeSpec(id=id_, behavior="custom", layer=layer, **kw)


def test_propagate_activates_signal_lobe() -> None:
    classify = _spec(
        "classify",
        LAYER_COGNITION,
        signals=lambda ctx: {"is_question": 1.0 if ctx.get("is_question") else 0.0},
        signal_weights={"is_question": 1.0},
        min_activation=0.5,
    )
    res = propagate([classify], {"is_question": True}, weights={})
    assert res.activated == ["classify"]
    assert res.by_id["classify"]["activated"] is True


def test_propagate_below_threshold_stays_dark() -> None:
    classify = _spec(
        "classify",
        LAYER_COGNITION,
        signals=lambda ctx: {"x": 0.0},
        signal_weights={"x": 1.0},
        min_activation=0.5,
    )
    res = propagate([classify], {}, weights={})
    assert res.activated == []


def test_forward_dag_validation_rejects_backward_edge() -> None:
    a = _spec("a", LAYER_EXPRESSION, order=1)
    b = _spec("b", LAYER_COGNITION, order=0, edges={"a": 0.5})
    # b (cognition) -> a (expression) is forward: fine
    validate_network([a, b])
    # a (expression) -> b (cognition) is backward: rejected
    bad = _spec("a", LAYER_EXPRESSION, order=1, edges={"b": 0.5})
    try:
        validate_network([bad, b])
        raise AssertionError("expected ValueError for backward edge")
    except ValueError:
        pass


def test_recognize_and_resolve_path() -> None:
    research = PathSpec(
        name="research",
        members=("plan",),
        recognizer=lambda ctx: 1.0 if ctx.get("complex") else 0.0,
        threshold=0.5,
    )
    scores = recognize_paths({"complex": True}, [research])
    assert scores["research"] == 1.0
    resolved = resolve_path(scores, [research])
    assert resolved["name"] == "research"
    assert resolved["emergent"] is False


def test_resolve_path_emergent_when_nothing_clears() -> None:
    p = PathSpec(name="qna", members=(), recognizer=lambda ctx: 0.0, threshold=0.5)
    resolved = resolve_path(recognize_paths({}, [p]), [p])
    assert resolved["emergent"] is True
    assert resolved["name"] == "emergent"


def test_blackboard_rejects_raw_chunks() -> None:
    board = Blackboard()
    raw = ContextNode(id="c1", kind="kb_chunk", text="secret", scope=None)
    try:
        board.write_back("research", [raw])
    except ValueError:
        pass
    else:
        raise AssertionError("blackboard must reject raw chunk kinds")


def test_build_attention_lexical_selection() -> None:
    nodes = [
        ContextNode(id="n1", kind="fact", text="alpha beta gamma", scope=None),
        ContextNode(id="n2", kind="fact", text="zeta eta theta", scope=None),
    ]
    from agent_sdk.network.context_builder import merge_weights

    selected, _trace = build_attention(
        nodes,
        "alpha beta",
        None,
        weights=merge_weights(None),
        budget_tokens=1600,
        min_activation=0.0,
    )
    ids = {n.id for n in selected}
    assert "n1" in ids


def test_flow_step_and_flow_construct() -> None:
    flow = Flow(name="qna", steps=(FlowStep(name="synthesize", lobes=("synthesize",)),))
    assert flow.steps[0].type == "simple"


def test_lobe_registry_empty_default() -> None:
    reg = LobeRegistry()
    assert reg.lobes() == []


def test_merge_lobe_weights_sparse_override() -> None:
    merged = merge_lobe_weights({"prior_a": 0.1}, {"prior_a": 0.9, "bad": "x"})
    assert merged["prior_a"] == 0.9
    assert "bad" not in merged
