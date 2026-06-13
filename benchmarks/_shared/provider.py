"""Provider env for the LIVE benches — load the ``.env`` and resolve the model.

Standalone-SDK friendly: prefers the **SDK-local** ``packages/agent-sdk/.env``, falls back to the
repo-root ``.env``. Bridges MiniMax-native names (``MINIMAX_BASE_URL`` / ``MINIMAX_API_KEY``)
onto the Anthropic-compatible env the clients actually read (``ANTHROPIC_BASE_URL`` /
``ANTHROPIC_AUTH_TOKEN``) — MiniMax speaks the Anthropic protocol, so its endpoint is mounted at
``…/anthropic``. Returns the model name to drive ``make_client`` (or ``None`` if no credentials
are configured, so a bench can print a clean message and exit).
"""

from __future__ import annotations

import os
from pathlib import Path

SDK_ROOT = Path(__file__).resolve().parents[2]  # packages/agent-sdk
REPO_ROOT = SDK_ROOT.parents[1]

__all__ = ["load_provider"]


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _bridge_minimax() -> None:
    """Map ``MINIMAX_*`` onto the ``ANTHROPIC_*`` the (Anthropic-compatible) clients read."""
    key = os.environ.get("MINIMAX_API_KEY")
    if key and not (os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")):
        os.environ["ANTHROPIC_AUTH_TOKEN"] = key
    base = os.environ.get("MINIMAX_BASE_URL")
    if base and not os.environ.get("ANTHROPIC_BASE_URL"):
        base = base.rstrip("/")
        if not base.endswith("/anthropic"):
            base += "/anthropic"  # MiniMax mounts the Anthropic API under /anthropic
        os.environ["ANTHROPIC_BASE_URL"] = base


def load_provider() -> str | None:
    """Load the bench provider env (SDK-local first, then repo root) and return the model name,
    or ``None`` when no credentials are configured."""
    _load_env_file(SDK_ROOT / ".env")
    _load_env_file(REPO_ROOT / ".env")
    _bridge_minimax()
    if not (os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")):
        return None
    model = os.environ.get("ANTHROPIC_MODEL")
    if not model:
        # creds present but no model pinned → the SDK's MiniMax default if MiniMax, else Anthropic.
        model = "MiniMax-M2.7" if os.environ.get("MINIMAX_API_KEY") else "claude-opus-4-6"
    return model
