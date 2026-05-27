from __future__ import annotations

import unittest

from ipo_portal.normalize_v2.parsers.registry import ParserContext
from ipo_portal.orchestrator.cli import build_parser
from ipo_portal.normalize_v2.parsers.yahoo_performance import parse as parse_yahoo
from ipo_portal.yahoo_v2 import parse_chart


class YahooV2Tests(unittest.TestCase):
    def test_parse_chart_uses_first_trading_day_on_or_after_listing(self) -> None:
        payload = {
            "chart": {
                "result": [
                    {
                        "timestamp": [1709251200, 1709510400],
                        "meta": {
                            "currency": "INR",
                            "exchangeName": "NSI",
                            "instrumentType": "EQUITY",
                            "regularMarketPrice": 130.5,
                            "regularMarketTime": 1709510400,
                        },
                        "indicators": {
                            "quote": [
                                {
                                    "open": [None, 120.0],
                                    "close": [None, 125.25],
                                }
                            ]
                        },
                    }
                ]
            }
        }
        row = parse_chart(payload, "2024-03-02")
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["listing_candle_date"], "2024-03-04")
        self.assertEqual(row["listing_open_paise"], 12000)
        self.assertEqual(row["listing_close_paise"], 12525)
        self.assertEqual(row["current_price_paise"], 13050)

    def test_yahoo_parser_emits_v2_performance_contribution(self) -> None:
        body = [
            {
                "status": "ok",
                "company_name": "Example Limited",
                "symbol": "EXAMPLE",
                "yahoo_symbol": "EXAMPLE.NS",
                "listing_date": "2024-03-04",
                "issue_price_paise": 10000,
                "listing_open_paise": 12000,
                "listing_close_paise": 12500,
                "current_price_paise": 13000,
                "listing_gain_bps": 2500,
                "current_gain_from_issue_bps": 3000,
            }
        ]
        [contribution] = parse_yahoo(body, ParserContext("yahoo", "performance", "2026-05-24T00:00:00+00:00"))
        self.assertEqual(contribution.source, "yahoo")
        self.assertEqual(contribution.join_key.discriminator, "name_year")
        self.assertEqual(contribution.fields["listing_performance.current_price_paise"], 13000)
        self.assertEqual(contribution.fields["identity.aliases"], ["yahoo:EXAMPLE.NS"])

    def test_refresh_daily_accepts_skip_kite_flag(self) -> None:
        args = build_parser().parse_args(["refresh-daily", "--skip-kite", "--skip-enrich"])
        self.assertTrue(args.skip_kite)
        self.assertTrue(args.skip_enrich)


if __name__ == "__main__":
    unittest.main()
