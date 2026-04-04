# src/tangle/integrations/langgraph.py

import threading
from collections.abc import Callable
from functools import wraps
from typing import Any

import xxhash

from tangle.monitor import TangleMonitor
from tangle.types import AgentID, Event, EventType

_TANGLE_KEYS = {"tangle_workflow_id"}


def tangle_node(monitor: TangleMonitor, agent_id: AgentID):
    """Decorator that instruments a LangGraph node function."""
    _registered: set[str] = set()
    _registered_lock = threading.Lock()

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapper(state: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
            workflow_id = state.get("tangle_workflow_id", "default")
            reg_key = f"{workflow_id}:{agent_id}"

            with _registered_lock:
                if reg_key not in _registered:
                    monitor.process_event(
                        Event(
                            type=EventType.REGISTER,
                            timestamp=monitor.clock(),
                            workflow_id=workflow_id,
                            from_agent=agent_id,
                        )
                    )
                    _registered.add(reg_key)

            try:
                result = fn(state, *args, **kwargs)
            except Exception:
                monitor.process_event(
                    Event(
                        type=EventType.CANCEL,
                        timestamp=monitor.clock(),
                        workflow_id=workflow_id,
                        from_agent=agent_id,
                    )
                )
                raise

            if isinstance(result, dict):
                for key in result:
                    if key not in _TANGLE_KEYS:
                        body = xxhash.xxh128(
                            f"{key}={repr(result[key])}".encode()
                        ).digest()
                        monitor.process_event(
                            Event(
                                type=EventType.SEND,
                                timestamp=monitor.clock(),
                                workflow_id=workflow_id,
                                from_agent=agent_id,
                                to_agent="__graph__",
                                resource=key,
                                message_body=body,
                            )
                        )

            return result

        return wrapper

    return decorator


def tangle_conditional_edge(monitor: TangleMonitor, from_agent: AgentID):
    """Decorator for LangGraph conditional edge functions."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapper(state: dict[str, Any], *args: Any, **kwargs: Any) -> str:
            result = fn(state, *args, **kwargs)
            workflow_id = state.get("tangle_workflow_id", "default")

            if result and result != "__end__":
                monitor.process_event(
                    Event(
                        type=EventType.WAIT_FOR,
                        timestamp=monitor.clock(),
                        workflow_id=workflow_id,
                        from_agent=from_agent,
                        to_agent=result,
                        resource="conditional_edge",
                    )
                )
                monitor.process_event(
                    Event(
                        type=EventType.RELEASE,
                        timestamp=monitor.clock(),
                        workflow_id=workflow_id,
                        from_agent=from_agent,
                        to_agent=result,
                    )
                )

            return result

        return wrapper

    return decorator
