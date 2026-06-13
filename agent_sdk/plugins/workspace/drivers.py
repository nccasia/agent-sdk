"""Workspace backends owned by the workspace plugin — virtual / local / s3.

Each implements the ``Workspace`` protocol (``read``/``write``/``list``/``edit``).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

__all__ = ["VirtualWorkspace", "LocalWorkspace", "S3Workspace", "DRIVERS"]


class VirtualWorkspace:
    """Ephemeral in-memory file tree."""

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    async def read(self, path: str) -> bytes:
        if path not in self._files:
            raise FileNotFoundError(path)
        return self._files[path]

    async def write(self, path: str, data: bytes) -> None:
        self._files[path] = data

    async def list(self, prefix: str = "") -> list[str]:
        return sorted(p for p in self._files if p.startswith(prefix))

    async def edit(self, path: str, patch: str) -> None:
        cur = self._files.get(path, b"").decode("utf-8")
        self._files[path] = (cur + patch).encode("utf-8")


class LocalWorkspace:
    """Disk-backed file tree rooted at ``root`` (paths are sandboxed under it)."""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def _abs(self, path: str) -> str:
        full = os.path.abspath(os.path.join(self.root, path.lstrip("/")))
        if not full.startswith(self.root):
            raise ValueError(f"path escapes workspace root: {path!r}")
        return full

    def _read_sync(self, path: str) -> bytes:
        with open(self._abs(path), "rb") as f:
            return f.read()

    def _write_sync(self, path: str, data: bytes) -> None:
        full = self._abs(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)

    def _list_sync(self, prefix: str) -> list[str]:
        out: list[str] = []
        for dirpath, _dirs, files in os.walk(self.root):
            for name in files:
                rel = os.path.relpath(os.path.join(dirpath, name), self.root)
                if rel.startswith(prefix):
                    out.append(rel)
        return sorted(out)

    async def read(self, path: str) -> bytes:
        return await asyncio.to_thread(self._read_sync, path)

    async def write(self, path: str, data: bytes) -> None:
        await asyncio.to_thread(self._write_sync, path, data)

    async def list(self, prefix: str = "") -> list[str]:
        return await asyncio.to_thread(self._list_sync, prefix)

    async def edit(self, path: str, patch: str) -> None:
        try:
            cur = (await self.read(path)).decode("utf-8")
        except FileNotFoundError:
            cur = ""
        await self.write(path, (cur + patch).encode("utf-8"))


class S3Workspace:
    """S3-backed workspace (lazy boto3). ``read``/``write``/``list``/``edit``."""

    def __init__(self, bucket: str, *, prefix: str = "", client: Any | None = None):
        self.bucket = bucket
        self.prefix = prefix
        self._client = client

    def _conn(self) -> Any:
        if self._client is None:
            import boto3

            self._client = boto3.client("s3")
        return self._client

    def _key(self, path: str) -> str:
        return f"{self.prefix}{path}"

    async def read(self, path: str) -> bytes:
        obj = self._conn().get_object(Bucket=self.bucket, Key=self._key(path))
        return obj["Body"].read()

    async def write(self, path: str, data: bytes) -> None:
        self._conn().put_object(Bucket=self.bucket, Key=self._key(path), Body=data)

    async def list(self, prefix: str = "") -> list[str]:
        resp = self._conn().list_objects_v2(Bucket=self.bucket, Prefix=self._key(prefix))
        return sorted(o["Key"][len(self.prefix) :] for o in resp.get("Contents", []))

    async def edit(self, path: str, patch: str) -> None:
        try:
            cur = (await self.read(path)).decode("utf-8")
        except Exception:
            cur = ""
        await self.write(path, (cur + patch).encode("utf-8"))


DRIVERS = {"virtual": VirtualWorkspace, "local": LocalWorkspace, "s3": S3Workspace}
