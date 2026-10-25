from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, Mock, patch

import nonebot

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="nonebot.drivers.none:Driver")

from plugins.bot_admin import groups_admin


class GroupFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_groups_are_mapped_and_sorted(self) -> None:
        bot = Mock()
        bot.call_api = AsyncMock(
            return_value=[
                {"group_id": 2, "group_name": "b 群", "member_count": 5},
                {"group_id": 1, "group_name": "A 群"},
                {"group_id": "not-numeric"},
                {"group_id": 3, "group_name": "b 群", "member_count": "7"},
                "junk",
            ]
        )
        with patch("nonebot.get_bots", return_value={"1": bot}):
            rows = await groups_admin._fetch_groups()

        self.assertEqual([row["id"] for row in rows], ["1", "2", "3"])
        self.assertEqual(rows[0], {"id": "1", "name": "A 群", "member_count": 0})
        self.assertEqual(rows[1]["member_count"], 5)
        self.assertEqual(rows[2]["member_count"], 7)
        bot.call_api.assert_awaited_once_with("get_group_list")

    async def test_missing_bot_raises(self) -> None:
        with patch("nonebot.get_bots", return_value={}):
            with self.assertRaises(RuntimeError):
                await groups_admin._fetch_groups()

    async def test_duplicate_groups_are_deduped(self) -> None:
        bot = Mock()
        bot.call_api = AsyncMock(
            return_value=[
                {"group_id": 10, "group_name": "群十"},
                {"group_id": 10, "group_name": "群十（重复）"},
            ]
        )
        with patch("nonebot.get_bots", return_value={"1": bot}):
            rows = await groups_admin._fetch_groups()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "群十")


class GroupListStateTests(unittest.TestCase):
    def test_state_wraps_submit_coroutine(self) -> None:
        payload = [{"id": "123", "name": "测试群", "member_count": 3}]
        with patch(
            "plugins.astrbot_compat.runtime.submit_coroutine", Mock(return_value=payload)
        ) as submit:
            state = groups_admin.group_list_state()

        self.assertEqual(state, {"groups": payload})
        submit.assert_called_once()
        # 协程参数必须被真正交给 submit，而不是在后台线程里泄漏
        self.assertTrue(hasattr(submit.call_args.args[0], "close"))
        submit.call_args.args[0].close()


if __name__ == "__main__":
    unittest.main()
