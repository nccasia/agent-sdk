"""Guardrail error type (its own module so post-check factories can import it
without a cycle through the package ``__init__``)."""

from __future__ import annotations

__all__ = ["GuardrailError"]


class GuardrailError(Exception):
    """Raised by a guardrail check to block a turn."""
