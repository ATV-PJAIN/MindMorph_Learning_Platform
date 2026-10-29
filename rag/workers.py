"""Bound synchronous retrieval/model work without an unbounded queue of timed-out jobs."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import BoundedSemaphore

_pool = ThreadPoolExecutor(max_workers=12, thread_name_prefix="evidence-local")
_slots = BoundedSemaphore(12)


def bounded_call(function, timeout=8):
    if not _slots.acquire(blocking=False):
        raise TimeoutError("Retrieval workers are busy")
    context = copy_context()
    try:
        future = _pool.submit(context.run, function)
    except BaseException:
        _slots.release()
        raise
    # An async/thread timeout does not kill native work. Keep its admission slot until
    # it actually finishes; subsequent requests cannot grow an unlimited worker queue.
    future.add_done_callback(lambda _: _slots.release())
    return future.result(timeout=timeout)
