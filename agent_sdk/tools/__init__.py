"""The ``@tool`` decorator — a typed function becomes an Anthropic-compatible tool.

``@tool`` introspects a function's signature, type hints, and docstring into a
``{name, description, input_schema}`` spec and wraps it so it can run inside the
engine's tool loop. Sync and async functions both work; a single Pydantic model
parameter is treated as the structured argument (its JSON Schema becomes the
input schema).

    @tool
    async def search(query: str, top_k: int = 5) -> str:
        "Search the knowledge base."          # docstring → description

    class Ticket(BaseModel): title: str; priority: int = 3

    @tool(name="tickets.create", requires=["acl"])
    async def create_ticket(args: Ticket) -> str: ...

The decorated object is a :class:`Tool` (still callable, delegating to the
function). Pass a list of them to ``PreactAgent(tools=[...])`` or wrap them in a
:class:`FunctionToolRuntime`, which implements the ``ToolRuntime`` protocol.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import typing
from collections.abc import Callable, Sequence
from typing import Any, get_args, get_origin

try:  # pydantic is a hard dep, but keep the import defensive for clarity
    from pydantic import BaseModel
except Exception:  # pragma: no cover
    BaseModel = None  # type: ignore[assignment]

__all__ = ["tool", "Tool", "FunctionToolRuntime"]

_PRIMITIVE_SCHEMA: dict[Any, dict] = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    list: {"type": "array"},
    dict: {"type": "object"},
    Any: {},
}


def _resolve_hints(fn: Callable[..., Any]) -> dict[str, Any]:
    """Resolve a function's type hints, tolerating PEP 563 string annotations.

    Builds a local namespace from the function's closure free variables so a
    tool defined in a nested scope (referencing a locally-defined Pydantic model)
    still resolves. Falls back to the raw ``__annotations__`` on failure.
    """
    localns: dict[str, Any] = {}
    closure = getattr(fn, "__closure__", None)
    if closure:
        for name, cell in zip(fn.__code__.co_freevars, closure, strict=False):
            with contextlib.suppress(ValueError):
                localns[name] = cell.cell_contents
    try:
        return typing.get_type_hints(fn, localns=localns or None)
    except Exception:
        return dict(getattr(fn, "__annotations__", {}) or {})


def _is_pydantic_model(tp: Any) -> bool:
    return BaseModel is not None and isinstance(tp, type) and issubclass(tp, BaseModel)


def _schema_for_annotation(tp: Any) -> dict:
    """Best-effort JSON-schema fragment for a type annotation."""
    if tp is inspect.Parameter.empty or tp is None:
        return {}
    origin = get_origin(tp)
    if origin is None:
        if tp in _PRIMITIVE_SCHEMA:
            return dict(_PRIMITIVE_SCHEMA[tp])
        if _is_pydantic_model(tp):
            return tp.model_json_schema()
        return {}
    # Optional[X] / Union[X, None]
    if origin is typing.Union:
        args = [a for a in get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return _schema_for_annotation(args[0])
        return {}
    if origin in (list, Sequence, tuple):
        args = get_args(tp)
        item = _schema_for_annotation(args[0]) if args else {}
        return {"type": "array", "items": item} if item else {"type": "array"}
    if origin is dict:
        return {"type": "object"}
    return {}


class Tool:
    """A typed function wrapped as an Anthropic-compatible tool."""

    def __init__(
        self,
        fn: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        requires: Sequence[str] = (),
    ):
        self.fn = fn
        self.name = name or fn.__name__
        self.description = (description or inspect.getdoc(fn) or "").strip()
        self.requires = tuple(requires)
        self.is_async = inspect.iscoroutinefunction(fn)
        self._sig = inspect.signature(fn)
        self._hints = _resolve_hints(fn)
        self._pydantic_arg: tuple[str, type] | None = None
        self.input_schema = self._build_input_schema()

    # ── schema generation ────────────────────────────────────────────────────
    def _build_input_schema(self) -> dict:
        params = [
            p
            for p in self._sig.parameters.values()
            if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
        ]
        # Single Pydantic-model parameter → its schema is the input schema.
        if len(params) == 1:
            ann = self._hints.get(params[0].name, params[0].annotation)
            if _is_pydantic_model(ann):
                self._pydantic_arg = (params[0].name, ann)
                schema = ann.model_json_schema()
                schema.setdefault("type", "object")
                return schema

        properties: dict[str, dict] = {}
        required: list[str] = []
        for p in params:
            ann = self._hints.get(p.name, p.annotation)
            frag = _schema_for_annotation(ann)
            if p.default is inspect.Parameter.empty:
                required.append(p.name)
            else:
                if p.default is not None:
                    frag = {**frag, "default": p.default}
            properties[p.name] = frag
        return {"type": "object", "properties": properties, "required": required}

    @property
    def spec(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    # ── validation ───────────────────────────────────────────────────────────
    def missing_required(self, inp: dict | None) -> list[str]:
        """Required schema properties absent from ``inp`` (empty ⇒ call is well-formed).

        Skips the single-Pydantic-model form, whose own ``model_validate`` already
        raises field-level validation errors on a bad payload.
        """
        if self._pydantic_arg is not None:
            return []
        inp = inp or {}
        required = self.input_schema.get("required", []) or []
        return [k for k in required if k not in inp]

    # ── invocation ───────────────────────────────────────────────────────────
    async def invoke(self, inp: dict | None) -> Any:
        inp = inp or {}
        if self._pydantic_arg is not None:
            arg_name, model = self._pydantic_arg
            kwargs = {arg_name: model.model_validate(inp)}
        else:
            valid = set(self._sig.parameters)
            kwargs = {k: v for k, v in inp.items() if k in valid}
        result = self.fn(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.fn(*args, **kwargs)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"Tool(name={self.name!r}, requires={self.requires})"


def tool(
    fn: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    requires: Sequence[str] = (),
) -> Any:
    """Decorate a typed function into a :class:`Tool`.

    Usable bare (``@tool``) or parameterized (``@tool(name=..., requires=[...])``).
    """

    def wrap(f: Callable[..., Any]) -> Tool:
        return Tool(f, name=name, description=description, requires=requires)

    if fn is not None:
        return wrap(fn)
    return wrap


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if BaseModel is not None and isinstance(value, BaseModel):
        return value.model_dump_json()
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


class FunctionToolRuntime:
    """A ``ToolRuntime`` over a list of ``@tool`` functions.

    Implements ``get_tool_specs`` / ``call_tool`` so a set of plain typed
    functions plugs straight into the engine's tool loop (and composes with KB /
    MCP runtimes via ``CompositeToolRuntime``).
    """

    def __init__(self, tools: Sequence[Tool | Callable[..., Any]] | None = None):
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.add(t)

    def add(self, t: Tool | Callable[..., Any]) -> None:
        if not isinstance(t, Tool):
            t = Tool(t)
        self._tools[t.name] = t

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def get_tool_specs(self) -> list[dict]:
        return [t.spec for t in self._tools.values()]

    async def call_tool(
        self,
        name: str,
        inp: dict,
        retrieved_chunks: list[dict] | None = None,
        already_read: set[str] | None = None,
    ) -> str:
        t = self._tools.get(name)
        if t is None:
            return f"Error: unknown tool '{name}'. Use only the provided tools."
        # Validate required args up front so a malformed call returns a clean,
        # model-actionable message (e.g. a missing `file_path`) instead of a raw
        # Python TypeError leaking the internal function qualname — which the
        # model can't act on and tends to repeat verbatim.
        missing = t.missing_required(inp)
        if missing:
            plural = "s" if len(missing) > 1 else ""
            return (
                f"Error: tool '{name}' requires argument{plural} "
                f"{', '.join(repr(m) for m in missing)}. Provide "
                f"{'them' if plural else 'it'} and call again."
            )
        try:
            result = await t.invoke(inp)
        except Exception as exc:  # surface tool errors to the model, don't crash the turn
            return f"Error calling tool '{name}': {exc}"
        return _stringify(result)
