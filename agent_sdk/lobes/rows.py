"""Declarative rows → callables (the G6 extend seam).

A new capability is a registry ROW, never an interpreter branch: rows carry
data ({"regex": …} | {"flag": …} | {"const": …}), and these compilers turn
them into deterministic extractors/recognizers. The `extend` attentionbench
mode proves the seam (toy lobe + toy path join via rows only; removal restores
the baseline byte-identically).
"""

from __future__ import annotations

import re


def compile_row_signals(spec: dict | None):
    """Compile a declarative signal spec (a registry row's ``signals`` value)
    into a deterministic extractor. Supported entries, keyed by signal name:

      {"regex": "<pattern>"}     1.0 when the pattern matches ctx["query"]
      {"flag": "<ctx key>"}      1.0 when ctx[key] is truthy
      {"const": <float>}         a constant

    Rows carry data, never code — a new capability is a row, not a branch.
    """
    spec = dict(spec or {})

    def _entry(name: str, rule: dict):
        if "regex" in rule:
            pattern = re.compile(str(rule["regex"]), re.IGNORECASE)
            return name, lambda ctx: 1.0 if pattern.search(str(ctx.get("query") or "")) else 0.0
        if "flag" in rule:
            key = str(rule["flag"])
            return name, lambda ctx: 1.0 if ctx.get(key) else 0.0
        const = float(rule.get("const", 0.0))
        return name, lambda ctx: const

    entries = [_entry(name, rule) for name, rule in spec.items() if isinstance(rule, dict)]

    def signals(ctx: dict) -> dict[str, float]:
        return {name: fn(ctx) for name, fn in entries}

    return signals


def compile_row_recognizer(rule: dict | None):
    """Compile a declarative path recognizer row ({"regex": …} | {"flag": …})."""
    rule = dict(rule or {})
    if "regex" in rule:
        pattern = re.compile(str(rule["regex"]), re.IGNORECASE)
        return lambda ctx: 1.0 if pattern.search(str(ctx.get("query") or "")) else 0.0
    if "flag" in rule:
        key = str(rule["flag"])
        return lambda ctx: 1.0 if ctx.get(key) else 0.0
    return lambda _ctx: 0.0
