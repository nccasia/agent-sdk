"""The ``fs.*`` tool runtime owned by the workspace plugin."""

from __future__ import annotations

import json

from agent_sdk.plugins.base import Workspace

__all__ = ["FsToolRuntime"]


class FsToolRuntime:
    """The ``fs.*`` tools over a bound :class:`Workspace`."""

    def __init__(self, workspace: Workspace):
        self.ws = workspace

    def get_tool_specs(self) -> list[dict]:
        s = lambda **p: {"type": "object", "properties": p, "required": list(p)}  # noqa: E731
        return [
            {
                "name": "fs.read",
                "description": "Read a workspace file.",
                "input_schema": s(path={"type": "string"}),
            },
            {
                "name": "fs.write",
                "description": "Write/overwrite a workspace file.",
                "input_schema": s(path={"type": "string"}, content={"type": "string"}),
            },
            {
                "name": "fs.list",
                "description": "List workspace files under a prefix.",
                "input_schema": {
                    "type": "object",
                    "properties": {"prefix": {"type": "string"}},
                    "required": [],
                },
            },
            {
                "name": "fs.edit",
                "description": "Edit a file: replace `find` with `replace`.",
                "input_schema": s(
                    path={"type": "string"}, find={"type": "string"}, replace={"type": "string"}
                ),
            },
        ]

    async def call_tool(self, name: str, inp: dict, retrieved_chunks=None, already_read=None) -> str:
        try:
            if name == "fs.read":
                return (await self.ws.read(inp["path"])).decode("utf-8")
            if name == "fs.write":
                await self.ws.write(inp["path"], str(inp.get("content", "")).encode("utf-8"))
                return f"Wrote {inp['path']}."
            if name == "fs.list":
                return json.dumps(await self.ws.list(inp.get("prefix", "")))
            if name == "fs.edit":
                cur = (await self.ws.read(inp["path"])).decode("utf-8")
                new = cur.replace(inp.get("find", ""), inp.get("replace", ""))
                await self.ws.write(inp["path"], new.encode("utf-8"))
                return f"Edited {inp['path']}."
        except Exception as exc:
            return f"Error: {exc}"
        return f"Error: unknown tool {name!r}."
