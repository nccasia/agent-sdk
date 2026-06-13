"""Per-turn data contracts passed to lobe classes and stage execution.

These are intentionally narrow lobe-runtime types, not the interpreter — they
carry conversation state and injected services so lobes can be tested without
constructing ``BotPolicyInterpreter``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from agent_sdk.contracts.services import LobeServices


@dataclass(frozen=True)
class PromptContribution:
    """One lobe-owned prompt block.

    ``stability`` is consumed by the interpreter's cache-aware composition:
    stable blocks are policy/config-like, slow blocks are durable recall, and
    volatile blocks are per-turn context.
    """

    text: str
    stability: str = "stable"
    stage_ids: tuple[str, ...] = ()
    source: str = ""


@dataclass
class TurnContext:
    """Per-turn data passed to lobe classes.

    This is intentionally a narrow lobe runtime context, not the interpreter.
    It carries conversation state and injected services so lobes can be tested
    without constructing ``BotPolicyInterpreter``.
    """

    query: str
    policy: Mapping[str, Any] = field(default_factory=dict)
    services: LobeServices = field(default_factory=LobeServices)
    stage_id: str | None = None
    active_path: str | None = None
    previous_path: str | None = None
    active_lobes: frozenset[str] = frozenset()
    blackboard: Any = None
    # Turn-scoped flash memory (RAM): the turn's working state (sub-questions,
    # goals, decisions, language, findings). Stages + the model (scratchpad.*
    # tools) read/write it to offload state out of the prompt and recall it
    # downstream. A ``Scratchpad`` instance; see agent_core/sdk/memory/scratchpad.py.
    scratchpad: Any = None
    lobe_outputs: dict[str, Any] = field(default_factory=dict)
    identity: Mapping[str, Any] = field(default_factory=dict)
    channel: Mapping[str, Any] = field(default_factory=dict)
    session_memory: Any = None
    memory_items: Sequence[Mapping[str, Any]] = ()
    task_items: Sequence[Mapping[str, Any]] = ()
    catalog_items: Sequence[Mapping[str, Any]] = ()
    # The turn's shared evidence channel: a KB-style ToolRuntime appends the
    # source chunks it retrieved to ``retrieved_chunks`` and records the chunk
    # ids it has surfaced in ``already_read`` (dedupe). The engine threads the
    # SAME two objects into every ``call_tool`` of the turn, so evidence
    # accumulates across stages/hops and a grounding lobe (cite/filter) can read
    # the pool via ``current_turn()``. Empty + ignored by tools that don't ground.
    retrieved_chunks: list[dict] = field(default_factory=list)
    already_read: set[str] = field(default_factory=set)
    # Infrastructure-degradation markers for the turn (domain-free). A host tool /
    # runtime appends ``"<area>:<status>"`` (e.g. ``"retrieval:no_readers"``) via
    # ``current_turn().degraded``; the engine surfaces them on ``Trace.degraded``.
    degraded: list[str] = field(default_factory=list)


@dataclass
class LobeResult:
    """Standard result envelope for class-based lobe execution."""

    value: Any = None
    nodes: list[Any] = field(default_factory=list)
    prompt: list[PromptContribution] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StageResult:
    """Phase 7+ — the result envelope for a single stage's execution.

    A stage's ReAct loop runs ONE LLM call (or one tool loop) using the
    system prompt composed by the lobe axis. The result carries:

    - ``stage_name`` / ``path`` — the stage's identity (for the trace).
    - ``text`` — the LLM's final answer (or the formatted payload for
      format-only stages).
    - ``context_nodes`` — the ContextNodes the lobe axis produced for
      this stage's system prompt (the "what the LLM saw").
    - ``tool_calls`` — tool invocations from the agentic loop (empty for
      ``loop="single"``).
    - ``tokens_in`` / ``tokens_out`` / ``latency_ms`` — for the trace
      and per-stage budgets.
    - ``metadata`` — per-stage dict (skipped: bool, hops: int, error: str
      | None, ...).
    """

    stage_name: str
    path: str
    text: str = ""
    context_nodes: list[Any] = field(default_factory=list)
    tool_calls: list[Any] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
