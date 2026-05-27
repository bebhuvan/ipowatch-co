"""Tests for the per-issue BSE detail + bid-summary parsers."""

from __future__ import annotations

import unittest

from ipo_portal.normalize_v2.parsers import bse_bid_summary, bse_issue_detail
from ipo_portal.normalize_v2.parsers.registry import ParserContext


CTX_DETAIL = ParserContext(source="bse", endpoint="issue_detail_9001", snapshot_at="2026-05-23T00:00:00+00:00")


class IssueDetailNamesTests(unittest.TestCase):
    def test_single_firm_with_caret_and_pipe_fields(self) -> None:
        # NAME^address|email|contact — only the name should survive.
        raw = "INTERACTIVE FINANCIAL SERVICES LIMITED^Office 508|cs@x.com|JAIN S"
        self.assertEqual(bse_issue_detail._names(raw), ["INTERACTIVE FINANCIAL SERVICES LIMITED"])

    def test_multiple_firms_semicolon(self) -> None:
        raw = "ALPHA CAPITAL LIMITED^addr; BETA SECURITIES LIMITED^addr2"
        self.assertEqual(
            bse_issue_detail._names(raw),
            ["ALPHA CAPITAL LIMITED", "BETA SECURITIES LIMITED"],
        )

    def test_email_dropped(self) -> None:
        self.assertEqual(bse_issue_detail._names("foo@bar.com"), None)

    def test_blank(self) -> None:
        self.assertIsNone(bse_issue_detail._names(""))


class IssueDetailParseTests(unittest.TestCase):
    def test_master_row_extraction(self) -> None:
        body = {
            "IPONO_0": [{
                "ScripName": "Acme Foods Limited",
                "IPO_NO": "9001",
                "Book_Running_Lead_Manager": "ACME CAPITAL LIMITED^addr|x@y.com",
                "Registrar": "BIGSHARE SERVICES PRIVATE LIMITED^addr",
                "Market_Lot": "1200",
                "Tick_Size": "1",
                "Face_Value": "10",
                "Price_Band": "91-95",
                "ScripCode": "544999",
            }],
            "IPONO_2": [{"Price": "91.00", "Quantity": "708000"}],
            "status": "success",
        }
        out = bse_issue_detail.parse(body, CTX_DETAIL)
        self.assertEqual(len(out), 1)
        f = out[0].fields
        self.assertEqual(f["pricing.market_lot"], 1200)
        self.assertEqual(f["pricing.face_value_paise"], 1000)        # ₹10 → 1000 paise
        self.assertEqual(f["pricing.price_band_lower_paise"], 9100)  # ₹91
        self.assertEqual(f["pricing.price_band_upper_paise"], 9500)  # ₹95
        self.assertEqual(f["parties.lead_managers"], ["ACME CAPITAL LIMITED"])
        self.assertEqual(f["parties.registrar"], "BIGSHARE SERVICES PRIVATE LIMITED")
        self.assertIn("bse:ipo_no:9001", f["identity.aliases"])
        self.assertEqual(len(f["book_building.demand_schedule"]), 1)

    def test_empty_master_returns_empty(self) -> None:
        self.assertEqual(bse_issue_detail.parse({"IPONO_0": []}, CTX_DETAIL), [])


class BidSummaryTests(unittest.TestCase):
    def _book_body(self, table_key: str) -> dict:
        return {
            table_key: [
                {"SRNo": "Sr.No.", "col2": "Category", "Scripname": "Acme Foods Limited"},
                {"SRNo": "1", "col2": "Qualified Institutional Buyers (QIBs)", "Scripname": "Acme Foods Limited",
                 "col3": "54000", "col4": "108000", "col5": "2.0000"},
                {"SRNo": "3", "col2": "Individual Investors", "Scripname": "Acme Foods Limited",
                 "col3": "126000", "col4": "378000", "col5": "3.0000"},
                {"SRNo": "", "col2": "Total", "Scripname": "Acme Foods Limited",
                 "col3": "180000", "col4": "486000", "col5": "2.7000"},
            ]
        }

    def test_consolidated_routes_to_consolidated(self) -> None:
        ctx = ParserContext(source="bse", endpoint="consolidated_bid_details_new_9001", snapshot_at="2026-05-23T00:00:00+00:00")
        out = bse_bid_summary.parse(self._book_body("table1"), ctx)
        f = out[0].fields
        self.assertIn("subscription.consolidated", f)
        self.assertEqual(f["subscription.consolidated"]["total_times_x"], "2.7000")
        cats = {c["category"]: c["times_x"] for c in f["subscription.consolidated"]["categories"]}
        self.assertEqual(cats.get("qib"), "2.0000")
        self.assertEqual(cats.get("retail"), "3.0000")
        self.assertEqual(f["subscription.overall_times_x"], "2.7000")

    def test_per_exchange_routes_to_by_exchange(self) -> None:
        ctx = ParserContext(source="bse", endpoint="bid_details_9001", snapshot_at="2026-05-23T00:00:00+00:00")
        out = bse_bid_summary.parse(self._book_body("table2"), ctx)
        f = out[0].fields
        # Dotted leaf path so NSE + BSE books coexist after merge.
        self.assertIn("subscription.by_exchange.bse", f)
        self.assertEqual(f["subscription.by_exchange.bse"]["total_times_x"], "2.7000")
        self.assertNotIn("subscription.consolidated", f)


if __name__ == "__main__":
    unittest.main()
