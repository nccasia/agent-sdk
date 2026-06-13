"""Declarative signal expressions — the host-language-free activation grammar.

A ``signal`` is a small JSON expression evaluated against a ``context`` dict to a
float in ``[0, 1]``. Keeping activation declarative (rather than a Python
closure) is what lets the deterministic core serialize into ``preact.spec.json``
and port byte-identically to Rust/Go/JS (see ``docs/porting.md`` §3).

Grammar::

    {"const": 1.0}                  constant
    {"flag": "is_question"}         context[flag] truthy → 1.0 else 0.0
    {"lexical": ["compare", "vs"]}  any term present in the query → 1.0
    {"min_words": 8}                query word count >= n → 1.0
    {"regex": "\\?$"}               query matches → 1.0
    {"all": [<expr>, ...]}          min() of children (AND)
    {"any": [<expr>, ...]}          max() of children (OR)
    {"not": <expr>}                 1 - child
    {"scale": [<expr>, 0.6]}        child * weight
    {"sum": [<expr>, ...]}          clamped Σ

``compile_signal`` turns an expression into a pure ``Callable[[dict], float]``;
``eval_signal`` evaluates one in place. Both are free and deterministic — no
clock, no I/O, no LLM. A bare number compiles to a constant; ``None`` to ``0.0``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

__all__ = ["compile_signal", "eval_signal", "SignalError"]

SignalExpr = "dict | float | int | bool | None"


class SignalError(ValueError):
    """A malformed signal expression."""


def _clamp(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _query(ctx: dict) -> str:
    return str(ctx.get("query", "") or "")


def _word_count(ctx: dict) -> int:
    wc = ctx.get("word_count")
    if isinstance(wc, int):
        return wc
    return len(_query(ctx).split())


def compile_signal(expr: Any) -> Callable[[dict], float]:
    """Compile a declarative signal expression to a pure ``(ctx) -> float``.

    Raises :class:`SignalError` for unknown operators or malformed shapes so a
    bad spec fails at load time, not silently at activation time.
    """
    # Bare scalars and None — the common "always on" / "dark" cases.
    if expr is None:
        return lambda _ctx: 0.0
    if isinstance(expr, bool):
        bv = 1.0 if expr else 0.0
        return lambda _ctx: bv
    if isinstance(expr, (int, float)):
        nv = _clamp(float(expr))
        return lambda _ctx: nv
    if not isinstance(expr, dict) or len(expr) != 1:
        raise SignalError(f"signal expression must be a scalar or a single-key dict, got {expr!r}")

    ((op, arg),) = expr.items()

    if op == "const":
        v = _clamp(float(arg))
        return lambda _ctx: v

    if op == "flag":
        key = str(arg)
        return lambda ctx: 1.0 if ctx.get(key) else 0.0

    if op == "lexical":
        terms = [str(t).lower() for t in (arg or [])]
        return lambda ctx: 1.0 if any(t in _query(ctx).lower() for t in terms) else 0.0

    if op == "min_words":
        n = int(arg)
        return lambda ctx: 1.0 if _word_count(ctx) >= n else 0.0

    if op == "regex":
        pat = re.compile(str(arg))
        return lambda ctx: 1.0 if pat.search(_query(ctx)) else 0.0

    if op == "all":
        children = [compile_signal(c) for c in (arg or [])]
        if not children:
            return lambda _ctx: 1.0  # vacuous AND
        return lambda ctx: min(c(ctx) for c in children)

    if op == "any":
        children = [compile_signal(c) for c in (arg or [])]
        if not children:
            return lambda _ctx: 0.0  # vacuous OR
        return lambda ctx: max(c(ctx) for c in children)

    if op == "not":
        child = compile_signal(arg)
        return lambda ctx: _clamp(1.0 - child(ctx))

    if op == "scale":
        if not isinstance(arg, (list, tuple)) or len(arg) != 2:
            raise SignalError("scale expects [<expr>, weight]")
        child = compile_signal(arg[0])
        weight = float(arg[1])
        return lambda ctx: _clamp(child(ctx) * weight)

    if op == "sum":
        children = [compile_signal(c) for c in (arg or [])]
        return lambda ctx: _clamp(sum(c(ctx) for c in children))

    raise SignalError(f"unknown signal operator {op!r}")


def eval_signal(expr: Any, ctx: dict) -> float:
    """Evaluate a signal expression against a context (compile + call)."""
    return compile_signal(expr)(ctx)
