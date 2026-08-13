"""有界线程池：把阻塞 IO / CPU 密集段移出事件循环。

设计目标：
- 事件循环永不因同步代码卡死（用户消息处理不被阻塞）
- 线程数量有界可控，避免并发峰值时线程爆炸
- 所有需要线程池的地方统一走 run_blocking()，不散落裸 Thread(...)
- 启动时把事件循环默认 executor 设为同一个有界池，
  让现有 asyncio.to_thread 调用也受 max_workers 约束

用法：
    from core.concurrency import run_blocking

    # 在 async handler 里执行同步函数，不阻塞事件循环：
    result = await run_blocking(some_sync_fn, arg1, arg2, key=value)
"""

from __future__ import annotations

import asyncio
import functools
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

logger = logging.getLogger("HikariBot.Concurrency")

T = TypeVar("T")

DEFAULT_MAX_WORKERS = 8

_pool: ThreadPoolExecutor | None = None
_pool_max_workers: int = DEFAULT_MAX_WORKERS
_pool_lock = threading.Lock()


def _create_pool_locked(workers: int) -> ThreadPoolExecutor:
    """创建线程池并替换全局引用。调用方必须已持有 _pool_lock。"""
    global _pool
    old = _pool
    pool = ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="hikari-blocking",
    )
    _pool = pool
    if old is not None:
        # 不用 cancel_futures：排队未开始的任务仍会在旧池上跑完，
        # 等待中的 run_blocking 协程不会抛 CancelledError。
        # 注意：本函数目前只在启动时调用一次、无在途任务；
        # 若未来支持运行时热重载，需评估旧池替换瞬间的语义。
        old.shutdown(wait=False)
    logger.info("[Concurrency] 有界线程池已配置 max_workers=%d", workers)
    return pool


def configure_executor(max_workers: int | None = None) -> ThreadPoolExecutor:
    """创建（或按需重建）有界线程池。启动时调用一次即可。

    Args:
        max_workers: 线程池上限；None 或 <=0 时使用默认值。
    """
    global _pool_max_workers
    workers = max(1, int(max_workers) if max_workers else DEFAULT_MAX_WORKERS)

    # 注意：threading.Lock 不可重入，锁内绝不能再调用会加锁的函数。
    # _pool 始终指向存活池（旧池替换时才 shutdown），无需检查私有 _shutdown 标志
    with _pool_lock:
        if _pool is not None and _pool_max_workers == workers:
            return _pool
        _pool_max_workers = workers
        return _create_pool_locked(workers)


def executor() -> ThreadPoolExecutor:
    """返回当前有界线程池（未配置时按上次配置值懒创建）。"""
    with _pool_lock:
        if _pool is None:
            return _create_pool_locked(_pool_max_workers)
        return _pool


def setup_default_executor() -> None:
    """把当前事件循环的默认 executor 设为有界池（须在运行中的循环里调用）。

    这样 asyncio.to_thread(...) 也会走同一个有界池，全局并发受控。
    """
    loop = asyncio.get_running_loop()
    pool = executor()
    try:
        if loop.default_executor is not pool:
            loop.set_default_executor(pool)
            logger.debug("[Concurrency] 事件循环默认 executor 已设为有界线程池")
    except (RuntimeError, AttributeError):
        logger.debug("[Concurrency] 当前循环不支持设置默认 executor，忽略")


async def run_blocking(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """在事件循环外执行同步函数并返回结果，期间不阻塞事件循环。

    适合：PIL 图像渲染、无异步实现的同步库调用、大文件 IO 等。
    在线程内不要触碰 asyncio 原语 / NoneBot 发送 API —— 结果带回事件循环再发送。
    """
    call = functools.partial(fn, *args, **kwargs)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor(), call)
