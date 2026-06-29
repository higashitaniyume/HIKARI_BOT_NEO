from __future__ import annotations

import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from plugins.steam_deals import storage as steam_storage
from plugins.steam_deals.api import SteamDeal, SteamDealsClient


SAMPLE_CONFIG = {
    "api_url": "https://store.steampowered.com/api/featuredcategories",
    "country": "cn",
    "language": "schinese",
    "timeout": 20,
    "max_items": 10,
    "max_low_price_cents": 1000,
    "min_discount_percent": 90,
}


class SteamDealsParsingTests(unittest.TestCase):
    def test_parse_and_filter_free_low_and_big_discount(self) -> None:
        client = SteamDealsClient(SAMPLE_CONFIG)
        deals = client._parse_featured_categories(
            {
                "specials": {
                    "items": [
                        {
                            "id": 10,
                            "type": 0,
                            "name": "Free Game",
                            "final_price": 0,
                            "original_price": 6800,
                            "discount_percent": 100,
                            "currency": "CNY",
                        },
                        {
                            "id": 20,
                            "type": 0,
                            "name": "Cheap Game",
                            "final_price": 900,
                            "original_price": 9000,
                            "discount_percent": 90,
                            "currency": "CNY",
                        },
                        {
                            "id": 30,
                            "type": 0,
                            "name": "Normal Game",
                            "final_price": 3300,
                            "original_price": 6600,
                            "discount_percent": 50,
                            "currency": "CNY",
                        },
                    ]
                }
            }
        )

        result = client.filter_deals(deals, "all")

        self.assertEqual([item.appid for item in result], [10, 20])
        self.assertIn("免费", result[0].categories)
        self.assertIn("低价", result[1].categories)
        self.assertIn("大折扣", result[1].categories)

    def test_filter_modes(self) -> None:
        client = SteamDealsClient(SAMPLE_CONFIG)
        deals = client._parse_featured_categories(
            {
                "specials": {
                    "items": [
                        {"id": 10, "type": 0, "name": "Free", "final_price": 0, "discount_percent": 100},
                        {"id": 20, "type": 0, "name": "Low", "final_price": 500, "discount_percent": 70},
                    ]
                }
            }
        )

        self.assertEqual([item.appid for item in client.filter_deals(deals, "free")], [10])
        self.assertEqual([item.appid for item in client.filter_deals(deals, "low")], [20])

    def test_parse_search_results_html(self) -> None:
        client = SteamDealsClient(SAMPLE_CONFIG)
        deals = client._merge_deals(
            [],
            client._fetch_search_deals_from_html(
                """
                <a href="https://store.steampowered.com/app/123/Test/" data-ds-appid="123" class="search_result_row">
                  <div class="search_capsule"><img src="https://cdn.example/app.jpg"></div>
                  <span class="title">Test Game</span>
                  <div class="search_released">2024 年 1 月 1 日</div>
                  <span class="search_review_summary positive" data-tooltip-html="特别好评&lt;br&gt;100 篇评测"></span>
                  <div class="search_price_discount_combined" data-price-final="480">
                    <div class="discount_pct">-90%</div>
                    <div class="discount_original_price">¥48.00</div>
                    <div class="discount_final_price">¥4.80</div>
                  </div>
                </a>
                """,
            ),
        )

        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0].appid, 123)
        self.assertEqual(deals[0].name, "Test Game")
        self.assertEqual(deals[0].final_price_cents, 480)
        self.assertEqual(deals[0].original_price_cents, 4800)
        self.assertEqual(deals[0].discount_percent, 90)
        self.assertEqual(deals[0].review_summary, "特别好评")
        self.assertEqual(deals[0].review_percent, 0)
        self.assertEqual(deals[0].review_count, 100)

    def test_parse_steamdb_free_promotions_html(self) -> None:
        client = SteamDealsClient(SAMPLE_CONFIG)
        deals = client._fetch_steamdb_promotions_from_html(
            """
            <table>
              <tr>
                <td><a href="/app/111/">Keep Game</a></td>
                <td>Free to Keep</td>
                <td><time datetime="2026-06-29T00:00:00Z">now</time></td>
                <td><time datetime="2026-07-01T00:00:00Z">soon</time></td>
              </tr>
              <tr>
                <td><a href="https://store.steampowered.com/app/222/Trial_Game/">Trial Game</a></td>
                <td>Play For Free</td>
                <td><time datetime="2026-06-29T00:00:00Z">now</time></td>
                <td><time datetime="2026-07-02T00:00:00Z">soon</time></td>
              </tr>
            </table>
            """
        )

        self.assertEqual([deal.appid for deal in deals], [111, 222])
        self.assertEqual(deals[0].promotion_kind, "free_to_keep")
        self.assertEqual(deals[0].promotion_end, "2026-07-01T00:00:00Z")
        self.assertEqual(deals[1].promotion_kind, "play_for_free")

    def test_daily_filter_limits_same_title_family(self) -> None:
        cfg = {
            **SAMPLE_CONFIG,
            "max_items": 10,
            "daily_filter": {
                "enabled": True,
                "max_per_title_family": 2,
                "min_review_count_for_plain_low_price": 20,
                "min_discount_for_plain_low_price": 80,
                "max_plain_low_price_items": 4,
                "require_recent_search_results": False,
            },
        }
        client = SteamDealsClient(cfg)
        deals = [
            SteamDeal(
                appid=100 + index,
                name=f"Barro {2020 + index}",
                url=f"https://store.steampowered.com/app/{100 + index}/",
                image_url="",
                discount_percent=90,
                original_price_cents=3000,
                final_price_cents=300,
                currency="",
                source="搜索",
                review_summary="特别好评",
                review_percent=90,
                review_count=100,
            )
            for index in range(5)
        ]
        deals.append(
            SteamDeal(
                appid=200,
                name="Different Game",
                url="https://store.steampowered.com/app/200/",
                image_url="",
                discount_percent=95,
                original_price_cents=6000,
                final_price_cents=300,
                currency="",
                source="搜索",
                review_summary="好评",
                review_percent=80,
                review_count=50,
            )
        )

        result = client.filter_deals(deals, "all")

        self.assertLessEqual(sum(1 for item in result if item.name.startswith("Barro")), 2)
        self.assertIn("Different Game", [item.name for item in result])

    def test_daily_filter_requires_recent_search_results(self) -> None:
        cfg = {
            **SAMPLE_CONFIG,
            "daily_filter": {
                "enabled": True,
                "max_per_title_family": 2,
                "min_review_count_for_plain_low_price": 20,
                "min_discount_for_plain_low_price": 80,
                "min_discount_for_recent_deal": 20,
                "max_plain_low_price_items": 4,
                "require_recent_search_results": True,
                "max_search_release_age_days": 730,
            },
        }
        client = SteamDealsClient(cfg)
        recent = date.today() - timedelta(days=30)
        deals = [
            SteamDeal(
                appid=301,
                name="Old Discount",
                url="https://store.steampowered.com/app/301/",
                image_url="",
                discount_percent=90,
                original_price_cents=3000,
                final_price_cents=300,
                currency="",
                source="搜索",
                released="2018 年 1 月 1 日",
                review_summary="特别好评",
                review_percent=90,
                review_count=1000,
            ),
            SteamDeal(
                appid=302,
                name="Recent Discount",
                url="https://store.steampowered.com/app/302/",
                image_url="",
                discount_percent=90,
                original_price_cents=3000,
                final_price_cents=300,
                currency="",
                source="搜索",
                released=f"{recent.year} 年 {recent.month} 月 {recent.day} 日",
                review_summary="好评",
                review_percent=80,
                review_count=30,
            ),
        ]

        result = client.filter_deals(deals, "all")

        self.assertEqual([item.name for item in result], ["Recent Discount"])

    def test_recent_discount_enters_daily_without_being_low_or_big_discount(self) -> None:
        cfg = {
            **SAMPLE_CONFIG,
            "daily_filter": {
                "enabled": True,
                "max_per_title_family": 2,
                "min_review_count_for_plain_low_price": 20,
                "min_discount_for_plain_low_price": 80,
                "min_discount_for_recent_deal": 20,
                "max_plain_low_price_items": 4,
                "require_recent_search_results": True,
                "max_search_release_age_days": 730,
            },
        }
        client = SteamDealsClient(cfg)
        recent = date.today() - timedelta(days=14)
        deals = [
            SteamDeal(
                appid=401,
                name="Recent Mid Discount",
                url="https://store.steampowered.com/app/401/",
                image_url="",
                discount_percent=25,
                original_price_cents=6800,
                final_price_cents=5100,
                currency="",
                source="搜索",
                released=f"{recent.year} 年 {recent.month} 月 {recent.day} 日",
                review_summary="好评",
                review_percent=80,
                review_count=15,
            ),
            SteamDeal(
                appid=402,
                name="Old Mid Discount",
                url="https://store.steampowered.com/app/402/",
                image_url="",
                discount_percent=25,
                original_price_cents=6800,
                final_price_cents=5100,
                currency="",
                source="搜索",
                released="2017 年 1 月 1 日",
                review_summary="好评",
                review_percent=80,
                review_count=1000,
            ),
        ]

        result = client.filter_deals(deals, "all")

        self.assertEqual([item.name for item in result], ["Recent Mid Discount"])
        self.assertIn("近期", result[0].categories)

    def test_price_snapshot_marks_new_and_deeper_discounts_after_baseline(self) -> None:
        with TemporaryDirectory() as tmp:
            original_path = steam_storage.STATE_PATH
            steam_storage.STATE_PATH = Path(tmp) / "steam_deals_state.json"
            try:
                first = [
                    SteamDeal(
                        appid=501,
                        name="Known Discount",
                        url="https://store.steampowered.com/app/501/",
                        image_url="",
                        discount_percent=20,
                        original_price_cents=5000,
                        final_price_cents=4000,
                        currency="",
                    )
                ]
                steam_storage.annotate_price_changes(first)

                self.assertNotIn("新打折", first[0].categories)
                self.assertNotIn("折扣加深", first[0].categories)

                second = [
                    SteamDeal(
                        appid=501,
                        name="Known Discount",
                        url="https://store.steampowered.com/app/501/",
                        image_url="",
                        discount_percent=50,
                        original_price_cents=5000,
                        final_price_cents=2500,
                        currency="",
                    ),
                    SteamDeal(
                        appid=502,
                        name="New Discount",
                        url="https://store.steampowered.com/app/502/",
                        image_url="",
                        discount_percent=30,
                        original_price_cents=6000,
                        final_price_cents=4200,
                        currency="",
                    ),
                ]
                steam_storage.annotate_price_changes(second)

                self.assertIn("折扣加深", second[0].categories)
                self.assertIn("新打折", second[1].categories)
            finally:
                steam_storage.STATE_PATH = original_path

    def test_daily_filter_keeps_old_game_when_discount_is_new(self) -> None:
        cfg = {
            **SAMPLE_CONFIG,
            "daily_filter": {
                "enabled": True,
                "max_per_title_family": 2,
                "min_review_count_for_plain_low_price": 20,
                "min_discount_for_plain_low_price": 80,
                "min_discount_for_recent_deal": 20,
                "max_plain_low_price_items": 4,
                "require_recent_search_results": True,
                "max_search_release_age_days": 730,
            },
        }
        client = SteamDealsClient(cfg)
        old_new_discount = SteamDeal(
            appid=601,
            name="Old Game Newly Discounted",
            url="https://store.steampowered.com/app/601/",
            image_url="",
            discount_percent=35,
            original_price_cents=6000,
            final_price_cents=3900,
            currency="",
            source="搜索",
            released="2017 年 1 月 1 日",
        )
        old_new_discount.categories.add("新打折")

        result = client.filter_deals([old_new_discount], "all")

        self.assertEqual([item.name for item in result], ["Old Game Newly Discounted"])

    def test_market_item_enters_daily_but_not_low_mode(self) -> None:
        client = SteamDealsClient(SAMPLE_CONFIG)
        market = SteamDeal(
            appid=701,
            name="Top Seller",
            url="https://store.steampowered.com/app/701/",
            image_url="",
            discount_percent=0,
            original_price_cents=6800,
            final_price_cents=6800,
            currency="",
            source="热卖",
            market_rank=1,
        )
        market.categories.add("热卖")
        normal = SteamDeal(
            appid=702,
            name="Normal Full Price",
            url="https://store.steampowered.com/app/702/",
            image_url="",
            discount_percent=0,
            original_price_cents=6800,
            final_price_cents=6800,
            currency="",
            source="搜索",
            released="2026 年 6 月 29 日",
        )

        self.assertEqual([item.name for item in client.filter_deals([normal, market], "all")], ["Top Seller"])
        self.assertEqual(client.filter_deals([market], "low"), [])


if __name__ == "__main__":
    unittest.main()
