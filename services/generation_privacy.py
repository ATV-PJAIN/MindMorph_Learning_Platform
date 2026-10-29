"""Keep generation inputs out of automatic traces and diagnostic logging.

Context variables propagate through asyncio.to_thread and LangGraph's executor context.
The log factory redacts at record creation, including queued handlers and exception text.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
import logging

_private = ContextVar("private_generation", default=False)
_previous_factory = logging.getLogRecordFactory()


def _record_factory(*args, **kwargs):
    record = _previous_factory(*args, **kwargs)
    # Some network libraries create their own threads without copying contextvars.
    # Their URL/debug logs can contain query text, even for public research.
    network_logger = record.name.split(".", 1)[0] in {"primp", "ddgs", "httpx", "httpx2", "httpcore"}
    if _private.get() or network_logger:
        arguments = record.args if isinstance(record.args, tuple) else ()
        exceptions = [arg for arg in arguments if isinstance(arg, BaseException)]
        if record.exc_info and record.exc_info[1]:
            exceptions.append(record.exc_info[1])
        diagnostics = [type(error).__name__ for error in exceptions]
        for error in exceptions:
            status = getattr(error, "status_code", None)
            if isinstance(status, int) and 100 <= status <= 599:
                diagnostics.append(f"HTTP {status}")
        # Preserve only numeric provider HTTP statuses, never arbitrary string arguments.
        if record.name == "llm_providers":
            diagnostics.extend(f"HTTP {arg}" for arg in arguments if type(arg) is int and 400 <= arg <= 599)
        record.msg = "Private generation activity (details withheld)" + ("; " + ", ".join(diagnostics) if diagnostics else "")
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
    return record


logging.setLogRecordFactory(_record_factory)


@contextmanager
def private_context():
    from langsmith import tracing_context
    token = _private.set(True)
    try:
        with tracing_context(enabled=False):
            yield
    finally:
        _private.reset(token)


def private_generation(function):
    if inspect.isasyncgenfunction(function):
        @wraps(function)
        async def stream(*args, **kwargs):
            iterator = function(*args, **kwargs)
            try:
                while True:
                    with private_context():
                        try:
                            item = await iterator.__anext__()
                        except StopAsyncIteration:
                            return
                    # Do not hold a context token across a yield to a consumer that
                    # may close the stream from a different task after disconnection.
                    yield item
            finally:
                with private_context():
                    await iterator.aclose()
        return stream
    if inspect.iscoroutinefunction(function):
        @wraps(function)
        async def asynchronous(*args, **kwargs):
            with private_context():
                return await function(*args, **kwargs)
        return asynchronous

    @wraps(function)
    def synchronous(*args, **kwargs):
        with private_context():
            return function(*args, **kwargs)
    return synchronous
