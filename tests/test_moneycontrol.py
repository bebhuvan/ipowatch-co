from __future__ import annotations

import unittest
from datetime import date

from ipo_portal.normalize import normalize_moneycontrol, normalize_snapshots


class MoneycontrolNormalizeTests(unittest.TestCase):
    def test_normalizes_listed_ipo_performance_row(self) -> None:
        snapshot = {
            "meta": {
                "source": "moneycontrol",
                "endpoint": "listed_ipos_page_000000",
                "fetched_at": "2026-05-22T09:00:00+00:00",
            },
            "body": {
                "success": 1,
                "data": {
                    "listedIpo": [
                        {
                            "sc_id": "RFP01",
                            "company_name": "RFBL Flexi Pack ",
                            "company_code": 14620279,
                            "url": "packaging-packaging-materials/rfblflexipack/RFP01",
                            "ipo_type": "SME",
                            "listing_date": "2026-05-19",
                            "issue_price": 50,
                            "issue_size": 353250000,
                            "total_subs": 20.45,
                            "last_price": "63.70",
                            "dt_open": 52.5,
                            "dt_close": 55.1,
                            "listing_gain": 5,
                            "todays_gain": 21.33,
                        }
                    ]
                },
            },
        }

        records = normalize_moneycontrol(snapshot, date(2026, 5, 22))

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.company_name, "RFBL Flexi Pack")
        self.assertEqual(record.status, "past")
        self.assertEqual(record.security_type, "SME")
        self.assertEqual(record.listing_date, "2026-05-19")
        self.assertEqual(record.issue_price, 50.0)
        self.assertEqual(record.issue_size_text, "35.33 crore")
        self.assertEqual(record.subscription_times, 20.45)
        self.assertEqual(record.listing_day_open, 52.5)
        self.assertEqual(record.listing_day_close, 55.1)
        self.assertEqual(record.listing_open_gain, 5.0)
        self.assertEqual(record.current_gain_from_listing_open, 21.33)
        self.assertEqual(record.gain_loss, None)
        self.assertEqual(record.stock_url, "https://www.moneycontrol.com/india/stockpricequote/packaging-packaging-materials/rfblflexipack/RFP01")

    def test_index_filters_stale_paginated_snapshots(self) -> None:
        index_snapshot = {
            "meta": {"source": "moneycontrol", "endpoint": "listed_ipos_index", "fetched_at": "2026-05-22T09:00:00+00:00"},
            "body": {"pages": [{"endpoint": "listed_ipos_page_000000"}]},
        }
        active_page = {
            "meta": {"source": "moneycontrol", "endpoint": "listed_ipos_page_000000", "fetched_at": "2026-05-22T09:00:00+00:00"},
            "body": {"success": 1, "data": {"listedIpo": [{"sc_id": "AAA", "company_name": "Active Ltd", "listing_date": "2026-05-01"}]}},
        }
        stale_page = {
            "meta": {"source": "moneycontrol", "endpoint": "listed_ipos_page_000020", "fetched_at": "2026-05-21T09:00:00+00:00"},
            "body": {"success": 1, "data": {"listedIpo": [{"sc_id": "OLD", "company_name": "Stale Ltd", "listing_date": "2025-01-01"}]}},
        }

        records = normalize_snapshots([index_snapshot, active_page, stale_page], date(2026, 5, 22))

        self.assertEqual([record["company_name"] for record in records], ["Active Ltd"])


if __name__ == "__main__":
    unittest.main()
