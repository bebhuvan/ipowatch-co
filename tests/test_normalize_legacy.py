from __future__ import annotations

import unittest
from datetime import date

from ipo_portal.normalize import normalize_nse, parse_date


class LegacyNormalizeTests(unittest.TestCase):
    def test_parse_date_rejects_malformed_year(self) -> None:
        self.assertIsNone(parse_date("07-Feb-0202"))

    def test_nse_public_issue_drops_impossible_listing_date(self) -> None:
        snapshot = {
            "meta": {"endpoint": "ipo_public_past_issues", "fetched_at": "2026-05-26T00:00:00+00:00"},
            "body": [
                {
                    "company": "Ruchi Soya Industries Limited",
                    "ipoEndDate": "28-MAR-2022",
                    "ipoStartDate": "24-MAR-2022",
                    "listingDate": "02-JAN-2003",
                    "symbol": "RUCHISOYA",
                }
            ],
        }

        [record] = normalize_nse(snapshot, date(2026, 5, 26))

        self.assertEqual(record.issue_end_date, "2022-03-28")
        self.assertIsNone(record.listing_date)


if __name__ == "__main__":
    unittest.main()
