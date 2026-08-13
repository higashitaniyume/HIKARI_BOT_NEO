"""core.concurrency —— 有界线程池 / run_blocking 单元测试。"""

import asyncio
import time
import unittest

from core.concurrency import configure_executor, executor, run_blocking


class RunBlockingTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_result(self):
        result = await run_blocking(lambda a, b: a + b, 2, 3)
        self.assertEqual(result, 5)

    async def test_passes_kwargs(self):
        result = await run_blocking(lambda a, b=0: a * b, 4, b=5)
        self.assertEqual(result, 20)

    async def test_propagates_exception(self):
        def boom():
            raise ValueError("boom")

        with self.assertRaises(ValueError):
            await run_blocking(boom)

    async def test_does_not_block_event_loop(self):
        """线程内阻塞 sleep 不应卡住事件循环上已调度的任务。"""
        order: list[str] = []

        async def ticker() -> None:
            await asyncio.sleep(0.05)
            order.append("tick")

        ticker_task = asyncio.ensure_future(ticker())
        order.append("before")
        await run_blocking(lambda: time.sleep(0.3))
        order.append("block_done")
        await ticker_task

        # 若 run_blocking 阻塞了事件循环，"tick" 会排在 "block_done" 之后
        self.assertEqual(order, ["before", "tick", "block_done"])

    def test_executor_is_bounded_and_reused(self):
        pool = configure_executor(4)
        try:
            self.assertEqual(pool._max_workers, 4)
            self.assertIs(executor(), pool)
        finally:
            pool.shutdown(wait=False)


if __name__ == "__main__":
    unittest.main()
