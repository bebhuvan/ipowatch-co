"""Tests for the NSE per-issue detail extraction (issueInfo.dataList)."""

from __future__ import annotations

import unittest

from ipo_portal.normalize_v2.parsers import nse_bid_summary as nse
from ipo_portal.normalize_v2.parsers.registry import ParserContext


CTX = ParserContext(source="nse", endpoint="issue_detail_acme_sme", snapshot_at="2026-05-24T00:00:00+00:00")


def _issue_info(rows: list[tuple[str, str]], heading: str = "Acme Labs Limited") -> dict:
    return {"issueInfo": {"heading": heading, "dataList": [{"title": t, "value": v} for t, v in rows]}}


class IssueInfoFieldTests(unittest.TestCase):
    def test_price_range_two_values(self) -> None:
        self.assertEqual(nse._price_range("Rs.132 to Rs.139 per equity share"), (13200, 13900))

    def test_price_range_single_value(self) -> None:
        self.assertEqual(nse._price_range("Rs.50"), (5000, 5000))

    def test_rupee_paise(self) -> None:
        self.assertEqual(nse._rupee_paise("Rs.10"), 1000)
        self.assertEqual(nse._rupee_paise("Re.1"), 100)

    def test_lot_size(self) -> None:
        self.assertEqual(nse._lead_int("1,000 Equity Shares"), 1000)

    def test_issue_period(self) -> None:
        self.assertEqual(nse._issue_period("21-May-2026 to 25-May-2026"), ("2026-05-21", "2026-05-25"))

    def test_firms_split_on_and(self) -> None:
        self.assertEqual(
            nse._firms("Hem Securities Limited and Share India Capital Services Private Limited"),
            ["Hem Securities Limited", "Share India Capital Services Private Limited"],
        )

    def test_firms_single(self) -> None:
        self.assertEqual(nse._firms("Narnolia Financial Services Limited"), ["Narnolia Financial Services Limited"])

    def test_http_url_only(self) -> None:
        self.assertEqual(nse._http_url("https://x/RHP.zip"), "https://x/RHP.zip")
        self.assertIsNone(nse._http_url("<a href=...>e-Forms</a>"))


class IssueDetailParseTests(unittest.TestCase):
    def test_detail_merged_with_symbol_key(self) -> None:
        body = _issue_info([
            ("Symbol", "ACME"),
            ("Issue Period", "21-May-2026 to 25-May-2026"),
            ("Price Range", "Rs.132 to Rs.139 per equity share"),
            ("Lot Size", "1000 Equity Shares"),
            ("Face Value", "Rs.10"),
            ("Tick Size", "Re.1"),
            ("Book Running Lead Managers", "Narnolia Financial Services Limited"),
            ("Sponsor Bank", "Axis Bank Limited"),
            ("Name of the Registrar", "Skyline Financial Services Private Limited"),
            ("Red Herring Prospectus", "https://nsearchives.nseindia.com/content/ipo/RHP_ACME.zip"),
        ])
        out = nse.parse(body, CTX)
        self.assertEqual(len(out), 1)
        f = out[0].fields
        self.assertEqual(out[0].join_key.discriminator, "symbol")
        self.assertEqual(f["identity.company_name"], "Acme Labs Limited")
        self.assertEqual(f["pricing.price_band_lower_paise"], 13200)
        self.assertEqual(f["pricing.price_band_upper_paise"], 13900)
        self.assertEqual(f["pricing.market_lot"], 1000)
        self.assertEqual(f["pricing.face_value_paise"], 1000)
        self.assertEqual(f["pricing.tick_size_paise"], 100)
        self.assertEqual(f["timeline.open_date"], "2026-05-21")
        self.assertEqual(f["timeline.close_date"], "2026-05-25")
        self.assertEqual(f["parties.lead_managers"], ["Narnolia Financial Services Limited"])
        self.assertEqual(f["parties.sponsor_bank"], "Axis Bank Limited")
        self.assertEqual(f["parties.registrar"], "Skyline Financial Services Private Limited")
        self.assertTrue(f["documents.rhp_url"].endswith("RHP_ACME.zip"))

    def test_bid_book_only_endpoint_emits_no_detail(self) -> None:
        # consolidated/bid_details have no issueInfo — detail stays absent.
        body = {"symbol": "ACME", "dataList": [
            {"srNo": "1", "category": "Qualified Institutional Buyers", "noOfShareOffered": "100", "noOfSharesBid": "200", "noOfTotalMeant": "2"},
        ]}
        out = nse.parse(body, ParserContext(source="nse", endpoint="consolidated_bid_details_acme", snapshot_at=CTX.snapshot_at))
        self.assertEqual(len(out), 1)
        f = out[0].fields
        self.assertIn("subscription.by_exchange.nse", f)
        self.assertNotIn("pricing.market_lot", f)
        self.assertNotIn("documents.rhp_url", f)


if __name__ == "__main__":
    unittest.main()
