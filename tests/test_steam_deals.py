from __future__ import annotations

import unittest

from plugins.steam_deals.api import SteamDealsClient


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


if __name__ == "__main__":
    unittest.main()
