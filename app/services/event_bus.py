import asyncio
from typing import Any, Callable, Dict, List
import structlog

logger = structlog.get_logger()

_subscribers: Dict[str, List[Callable]] = {}


def subscribe(event_type: str, handler: Callable) -> None:
    if event_type not in _subscribers:
        _subscribers[event_type] = []
    _subscribers[event_type].append(handler)


def unsubscribe(event_type: str, handler: Callable) -> None:
    if event_type in _subscribers:
        _subscribers[event_type] = [
            h for h in _subscribers[event_type] if h is not handler
        ]


async def publish(event_type: str, **kwargs: Any) -> None:
    handlers = _subscribers.get(event_type, [])
    for handler in handlers:
        try:
            result = handler(**kwargs)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.warning(
                "event_bus_handler_error",
                event_type=event_type,
                handler=handler.__name__,
                error=str(e),
            )


def clear() -> None:
    _subscribers.clear()
