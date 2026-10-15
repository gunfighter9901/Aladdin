"""Per-turn context for ADK tools.

state_delta is the primary channel. ContextVars cover the case where a tool
runs in the same task but the ADK state object is awkward to read.
"""

from __future__ import annotations

import contextvars
from typing import Any

_active_ids: contextvars.ContextVar[list[str]] = contextvars.ContextVar(
    "educator_active_source_ids",
    default=[],
)
_artifacts: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "educator_artifacts",
    default=None,
)


def set_active_source_ids(source_ids: list[str]) -> contextvars.Token[list[str]]:
    return _active_ids.set([str(item) for item in source_ids if item])


def reset_active_source_ids(token: contextvars.Token[list[str]]) -> None:
    _active_ids.reset(token)


def begin_artifacts() -> contextvars.Token[list[dict[str, Any]] | None]:
    return _artifacts.set([])


def reset_artifacts(token: contextvars.Token[list[dict[str, Any]] | None]) -> None:
    _artifacts.reset(token)


def push_artifact(kind: str, payload: dict[str, Any], tool_context: Any = None) -> None:
    """Record a studio artifact on both channels.

    ContextVar is the fast path when the tool runs in the same task.
    ToolContext.state survives executor/context switches where ContextVars
    do not propagate, so write there as well when available.
    """
    bucket = _artifacts.get()
    if bucket is not None:
        bucket.append({"kind": kind, "payload": payload})
    if tool_context is not None:
        try:
            state = getattr(tool_context, "state", None)
            if state is not None:
                existing = _read_state(state, "educator_artifacts")
                items = list(existing) if isinstance(existing, list) else []
                items.append({"kind": kind, "payload": payload})
                try:
                    if hasattr(state, "update"):
                        state.update({"educator_artifacts": items})
                    else:
                        setattr(state, "educator_artifacts", items)
                except Exception:
                    pass
        except Exception:
            pass


def current_artifacts(tool_state_items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Latest artifact of each kind produced during this turn."""
    found: dict[str, Any] = {}
    for item in _artifacts.get() or []:
        if isinstance(item, dict) and "kind" in item:
            found[item["kind"]] = item["payload"]
    # State channel covers tools that ran outside the ContextVar context.
    for item in tool_state_items or []:
        if isinstance(item, dict) and "kind" in item:
            found[item["kind"]] = item["payload"]
    return found


def active_source_ids(tool_context: Any = None) -> list[str]:
    if tool_context is not None:
        state = getattr(tool_context, "state", None)
        raw = _read_state(state, "active_source_ids")
        if raw:
            return [str(item) for item in raw if item]
    return list(_active_ids.get())


def _read_state(state: Any, key: str) -> Any:
    if state is None:
        return None
    try:
        value = state.get(key)
        if value:
            return value
    except Exception:
        pass
    return getattr(state, key, None)
