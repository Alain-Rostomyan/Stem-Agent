"""Tool registry.

Tools are plain Python functions decorated with `@tool`. The registry derives
the OpenAI-compatible JSON schema from each function's type hints, so the LLM
sees up-to-date tool descriptions without us hand-maintaining a parallel spec.

Custom tools written by the stem live under `tools/custom/<domain>/` and are
loaded with `load_custom_tools(paths)` at agent instantiation time.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, get_args, get_origin


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]      # JSON schema
    func: Callable[..., Any]
    is_custom: bool = False
    source_path: Optional[str] = None

    def openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


_REGISTRY: dict[str, ToolSpec] = {}


def _python_type_to_json(tp: Any) -> dict[str, Any]:
    """Best-effort mapping of Python type hints to JSON schema fragments."""
    if tp is inspect.Parameter.empty or tp is Any:
        return {}
    origin = get_origin(tp)
    if origin is None:
        if tp is str:
            return {"type": "string"}
        if tp is bool:
            return {"type": "boolean"}
        if tp is int:
            return {"type": "integer"}
        if tp is float:
            return {"type": "number"}
        if tp is list:
            return {"type": "array"}
        if tp is dict:
            return {"type": "object"}
        if tp is type(None):
            return {"type": "null"}
        # Fallback: treat as string.
        return {"type": "string"}

    # Optional[X] == Union[X, None]
    if origin is typing.Union:
        non_none = [a for a in get_args(tp) if a is not type(None)]
        if len(non_none) == 1:
            return _python_type_to_json(non_none[0])
        # Mixed unions — let it through as a permissive any.
        return {}
    if origin in (list, typing.List):
        args = get_args(tp)
        item = _python_type_to_json(args[0]) if args else {}
        return {"type": "array", "items": item or {"type": "string"}}
    if origin in (dict, typing.Dict):
        return {"type": "object"}
    return {}


def _build_parameters_schema(
    func: Callable[..., Any],
    param_descriptions: Optional[dict[str, str]],
) -> dict[str, Any]:
    sig = inspect.signature(func)
    properties: dict[str, Any] = {}
    required: list[str] = []
    descs = param_descriptions or {}
    for name, param in sig.parameters.items():
        if name == "self":
            continue
        prop = _python_type_to_json(param.annotation)
        if name in descs:
            prop["description"] = descs[name]
        properties[name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(name)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def tool(
    _func: Optional[Callable[..., Any]] = None,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    param_descriptions: Optional[dict[str, str]] = None,
) -> Callable[..., Any]:
    """Decorator that registers a function as an LLM-visible tool.

    `description` defaults to the function's docstring's first paragraph.
    `param_descriptions` is a per-arg description map; arg types come from
    Python type hints.
    """

    def wrap(func: Callable[..., Any]) -> Callable[..., Any]:
        tool_name = name or func.__name__
        doc = (func.__doc__ or "").strip()
        # Use the first paragraph (up to the first blank line) as the short
        # description, but include the full docstring so the LLM gets all the
        # detail. Empty docstrings default to a placeholder.
        desc = description or (doc if doc else f"Tool: {tool_name}")
        params = _build_parameters_schema(func, param_descriptions)
        spec = ToolSpec(
            name=tool_name,
            description=desc,
            parameters=params,
            func=func,
        )
        register(spec)
        return func

    if _func is not None:
        return wrap(_func)
    return wrap


def register(spec: ToolSpec) -> None:
    if spec.name in _REGISTRY and _REGISTRY[spec.name].source_path != spec.source_path:
        # Allow re-registration (e.g. re-import during tests) but warn against
        # collisions across files.
        existing = _REGISTRY[spec.name]
        if existing.source_path and spec.source_path and existing.source_path != spec.source_path:
            raise ValueError(
                f"tool {spec.name!r} already registered from {existing.source_path}, "
                f"refusing to re-register from {spec.source_path}"
            )
    _REGISTRY[spec.name] = spec


def get(name: str) -> ToolSpec:
    if name not in _REGISTRY:
        raise KeyError(f"tool {name!r} is not registered")
    return _REGISTRY[name]


def all_tools() -> dict[str, ToolSpec]:
    return dict(_REGISTRY)


def reset() -> None:
    """Clear the registry. Used by tests."""
    _REGISTRY.clear()


def load_starter_tools() -> list[str]:
    """Import every module under tools/starter/ so its @tool decorators run.

    Returns the list of registered starter tool names. Re-invocations after
    `reset()` use `importlib.reload` so the decorators re-execute (otherwise
    Python's module cache would silently leave the registry empty).
    """
    # Import here to avoid a circular import at module load.
    from tools import starter  # noqa: F401
    starter_dir = Path(starter.__file__).parent
    for py in sorted(starter_dir.glob("*.py")):
        if py.name == "__init__.py":
            continue
        mod_name = f"tools.starter.{py.stem}"
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])
        else:
            importlib.import_module(mod_name)
    return [name for name, spec in _REGISTRY.items() if not spec.is_custom]


def load_custom_tools(paths: list[str | Path]) -> list[str]:
    """Import a list of custom-tool python files and return tool names registered.

    `paths` are relative-to-repo paths like `tools/custom/qa/foo.py`.
    The loaded module is cached under `stem_custom_tools.<stem>` to avoid
    polluting the regular package namespace.
    """
    loaded: list[str] = []
    before = set(_REGISTRY)
    for raw in paths:
        p = Path(raw)
        if not p.exists():
            raise FileNotFoundError(f"custom tool file not found: {p}")
        mod_name = f"stem_custom_tools.{p.stem}"
        spec = importlib.util.spec_from_file_location(mod_name, p)
        if spec is None or spec.loader is None:
            raise ImportError(f"could not load custom tool from {p}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        # Tag whatever got registered while loading this file.
        new = set(_REGISTRY) - before
        for name in new:
            ts = _REGISTRY[name]
            ts.is_custom = True
            ts.source_path = str(p)
            loaded.append(name)
        before = set(_REGISTRY)
    return loaded


def openai_tool_specs(names: list[str]) -> list[dict[str, Any]]:
    """Return OpenAI-formatted tool specs for the given subset of tools."""
    out: list[dict[str, Any]] = []
    for n in names:
        out.append(get(n).openai_tool())
    return out
