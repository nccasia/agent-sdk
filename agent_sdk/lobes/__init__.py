"""Lobes — the lobe-network framework (domain-agnostic machinery).

The OY axis engine: the lobe base classes and the network that composes domain
lobes into a turn. Concrete lobes live in their DOMAIN packages
(``agent_sdk.skills.lobes``, ``agent_sdk.memory.lobes``, ``agent_sdk.cognition.lobes``,
``agent_sdk.expression.lobes``, ``agent_sdk.tools.lobes``); this package owns only the
framework they plug into:

- ``runtime``  — the lobe base classes (``BaseLobe`` / ``Lobe`` / ``TurnContext`` …).
- ``network``  — domain-driven aggregation into the default network.
- ``registry`` — ``LobeRegistry`` (per-turn view; row overrides).
- ``rows``     — compile declarative registry rows into signals/recognizers.
- ``weights``  — default weight surface.
- ``patterns`` — shared recognizer regexes.
"""
