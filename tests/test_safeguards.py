from __future__ import annotations

import unittest
from datetime import date
from tempfile import TemporaryDirectory
from pathlib import Path

from ipo_portal.normalize import merge_for_site
from ipo_portal.site_builder import build_astro_site_data
from ipo_portal.validate import validate_records


class SafeguardTests(unittest.TestCase):
    def test_observed_after_as_of_is_hard_error(self) -> None:
        records = [
            {
                "id": "late-observation",
                "source": "nse",
                "source_endpoint": "ipo_public_past_issues",
                "source_record_id": "ABC",
                "company_name": "ABC Limited",
                "issue_start_date": "2026-05-01",
                "issue_end_date": "2026-05-03",
                "observed_at": "2026-05-22T07:00:00+00:00",
            }
        ]

        report = validate_records(records, date(2026, 5, 1))

        self.assertIn("observed_after_as_of", {error["code"] for error in report["errors"]})

    def test_listing_before_issue_end_is_hard_error(self) -> None:
        records = [
            {
                "id": "bad-timeline",
                "source": "nse",
                "source_endpoint": "ipo_public_past_issues",
                "source_record_id": "ABC",
                "company_name": "ABC Limited",
                "issue_start_date": "2026-05-10",
                "issue_end_date": "2026-05-14",
                "listing_date": "2026-05-12",
                "observed_at": "2026-05-14T07:00:00+00:00",
            }
        ]

        report = validate_records(records, date(2026, 5, 14))

        self.assertIn("listing_before_issue_end", {error["code"] for error in report["errors"]})

    def test_site_redacts_future_sensitive_fields(self) -> None:
        records = [
            {
                "id": "future-sensitive",
                "source": "nse",
                "source_endpoint": "ipo_current_issue",
                "source_record_id": "ABC",
                "company_name": "ABC Limited",
                "issue_start_date": "2026-05-20",
                "issue_end_date": "2026-05-25",
                "listing_date": "2026-05-30",
                "issue_price": 100.0,
                "observed_at": "2026-05-22T07:00:00+00:00",
                "documents": [],
            }
        ]

        site_records = merge_for_site(records, date(2026, 5, 22))

        self.assertIsNone(site_records[0]["listing_date"])
        self.assertIsNone(site_records[0]["issue_price"])
        self.assertEqual(
            {redaction["field"] for redaction in site_records[0]["redactions"]},
            {"listing_date", "issue_price"},
        )

    def test_astro_bundle_splits_document_only_records(self) -> None:
        records = [
            {
                "id": "dated-past",
                "company_name": "ABC Limited",
                "documents": [],
                "exchange_platform": "NSE",
                "issue_end_date": "2026-01-03",
                "issue_price": 100,
                "issue_start_date": "2026-01-01",
                "issue_type": "IPO",
                "listing_date": "2026-01-10",
                "price_band_high": 100,
                "price_band_low": 90,
                "price_band_text": "90-100",
                "security_type": "Equity",
                "sources": [],
                "status": "past",
                "symbol": "ABC",
            },
            {
                "id": "document-only",
                "company_name": "XYZ Limited",
                "documents": [{"type": "DRHP", "url": "https://example.com/drhp.pdf"}],
                "exchange_platform": "BSE",
                "issue_end_date": None,
                "issue_start_date": None,
                "issue_type": "IPO",
                "sources": [],
                "status": "document",
            },
        ]

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_astro_site_data(root, records, {"errors": [], "warnings": []}, date(2026, 5, 22))

            self.assertTrue((root / "manifest.json").exists())
            self.assertIn("dated-past", (root / "issues" / "historical.json").read_text())
            self.assertNotIn("document-only", (root / "issues" / "historical.json").read_text())
            self.assertIn("document-only", (root / "issues" / "documents.json").read_text())


if __name__ == "__main__":
    unittest.main()
