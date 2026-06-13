"""SurfaceCache — lazy compile-on-activate persistence for compiled skill surfaces.

A skill's surface is built the first time it is ACTIVATED (never before — an unused
skill costs nothing) and cached here keyed by content hash, so every later
activation is free. Two layers:

- in-process dict (always on) — frees re-activation within a run;
- an optional ``SKILL.compiled.json`` sidecar next to the skill folder (when the
  pack carries a ``source_dir``) — frees the first activation of future runs too.

A sidecar whose ``content_hash`` no longer matches the skill is stale: ignored and
recompiled. Cache I/O is best-effort — any failure just means a recompile.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from agent_sdk.skills.compiler import CompiledSkill, content_hash

_SIDECAR = "SKILL.compiled.json"


class SurfaceCache:
    def __init__(self, *, persist: bool = True) -> None:
        self._mem: dict[str, CompiledSkill] = {}  # content_hash → compiled
        self._persist = persist  # write/read the sidecar (off ⇒ in-process only, e.g. A/B runs)

    def get(self, pack: Any) -> CompiledSkill | None:
        """A cached surface for ``pack`` at its CURRENT content hash, or None."""
        chash = content_hash(pack)
        hit = self._mem.get(chash)
        if hit is not None:
            return hit
        side = self._read_sidecar(pack)
        if side is not None and side.content_hash == chash:
            self._mem[chash] = side
            return side
        return None

    def put(self, pack: Any, compiled: CompiledSkill) -> None:
        self._mem[compiled.content_hash] = compiled
        self._write_sidecar(pack, compiled)

    # ── sidecar (folder skills only) ─────────────────────────────────────────
    def _sidecar_path(self, pack: Any) -> Path | None:
        if not self._persist:
            return None
        src = getattr(pack, "source_dir", None)
        return Path(src) / _SIDECAR if src else None

    def _read_sidecar(self, pack: Any) -> CompiledSkill | None:
        p = self._sidecar_path(pack)
        if p is None or not p.is_file():
            return None
        try:
            return CompiledSkill.from_json(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None

    def _write_sidecar(self, pack: Any, compiled: CompiledSkill) -> None:
        p = self._sidecar_path(pack)
        if p is None:
            return
        with contextlib.suppress(Exception):
            p.write_text(json.dumps(compiled.to_json(), ensure_ascii=False, indent=2),
                         encoding="utf-8")
