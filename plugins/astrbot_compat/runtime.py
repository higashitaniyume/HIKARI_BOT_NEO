"""Bridge synchronous worker threads to NoneBot's running event loop."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar


T = TypeVar("T")
_running_loop: asyncio.AbstractEventLoop | None = None
_loop_thread_id: int | None = None
_loop_lock = threading.Lock()


def set_running_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _running_loop, _loop_thread_id
    with _loop_lock:
        _running_loop = loop
        _loop_thread_id = threading.get_ident()


def clear_running_loop() -> None:
    global _running_loop, _loop_thread_id
    with _loop_lock:
        _running_loop = None
        _loop_thread_id = None


def submit_coroutine(coro: Coroutine[Any, Any, T], timeout: float = 300.0) -> T:
    """Submit a coroutine from a worker thread and wait for its result."""
    with _loop_lock:
        loop = _running_loop
        loop_thread_id = _loop_thread_id

    if loop is None or loop.is_closed() or not loop.is_running():
        coro.close()
        raise RuntimeError("NoneBot event loop is not available")
    if threading.get_ident() == loop_thread_id:
        coro.close()
        raise RuntimeError("Cannot synchronously wait on the NoneBot event loop thread")

    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=timeout)
    except BaseException:
        future.cancel()
        raise
