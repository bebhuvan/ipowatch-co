from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from ipo_portal.normalize_v2.parsers.registry import ParserContext
from ipo_portal.orchestrator.cli import build_parser
from ipo_portal.orchestrator.refresh import run_refresh
from ipo_portal.normalize_v2.parsers.yahoo_performance import parse as parse_yahoo
from ipo_portal.yahoo_v2 import candidates, parse_chart


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
        args = build_parser().parse_args(["refresh-daily", "--skip-kite", "--skip-yahoo", "--skip-enrich"])
        self.assertTrue(args.skip_kite)
        self.assertTrue(args.skip_yahoo)
        self.assertTrue(args.skip_enrich)

    def test_yahoo_candidates_can_read_committed_v3_tree(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            issue_dir = root / "issues" / "by-slug"
            issue_dir.mkdir(parents=True)
            (issue_dir / "example.json").write_text(
                """
                {
                  "slug": "example",
                  "identity": {
                    "aliases": [
                      "bse:stock_page:https://www.bseindia.com/stock-share-price/example-ltd/example/544412/"
                    ],
                    "board_type": "SME Board",
                    "company_name": "Example Limited",
                    "issue_type": "IPO",
                    "status": "Listed"
                  },
                  "pricing": {"issue_price_paise": 10000},
                  "timeline": {"listing_date": "2025-01-01"}
                }
                """,
                encoding="utf-8",
            )

            [candidate] = candidates(root)

        self.assertEqual(candidate.symbol, "544412")
        self.assertEqual(candidate.yahoo_symbol, "544412.BO")

    def test_refresh_bootstraps_site_v2_before_yahoo_on_clean_checkout(self) -> None:
        with TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            normalize_calls = []

            def fake_normalize(*, raw_root: Path, out_root: Path, schema_root: Path) -> None:
                normalize_calls.append((raw_root, out_root, schema_root))
                (out_root / "issues" / "by-slug").mkdir(parents=True, exist_ok=True)
                (out_root / "manifest.json").write_text(
                    '{"issues_published": 1, "issues_quarantined": 0, "companies_total": 1}\n',
                    encoding="utf-8",
                )

            def fake_yahoo_export(*, site_root: Path, data_root: Path) -> Path:
                self.assertTrue((site_root / "issues" / "by-slug").exists())
                snap = data_root / "raw" / "yahoo" / "performance" / "snapshot.json"
                snap.parent.mkdir(parents=True, exist_ok=True)
                snap.write_text('{"body": []}\n', encoding="utf-8")
                return snap

            def fake_export_v3(*, source_root: Path, out_root: Path, schema_root: Path):
                return SimpleNamespace(
                    issues=1,
                    companies=1,
                    trajectories=0,
                    prospectuses=1,
                    dataset_version="v3.test",
                )

            with (
                patch("ipo_portal.normalize_v2.pipeline.run_normalize", side_effect=fake_normalize),
                patch("ipo_portal.yahoo_v2.export_snapshot", side_effect=fake_yahoo_export),
                patch("ipo_portal.site_v3.export_v3", side_effect=fake_export_v3),
            ):
                summary = run_refresh(
                    skip_sebi=True,
                    skip_kite=True,
                    skip_tijori=True,
                    skip_enrich=True,
                    hot=True,
                    data_root=data_root,
                )

            self.assertTrue(summary["ok"])
            self.assertGreaterEqual(len(normalize_calls), 2)
            self.assertEqual(summary["steps"][3]["name"], "yahoo_prices")
            self.assertEqual(summary["steps"][3]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
