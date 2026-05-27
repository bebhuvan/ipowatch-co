from __future__ import annotations

import unittest

from ipo_portal.normalize_v2.parsers import bse_public_issue_details
from ipo_portal.normalize_v2.parsers.registry import ParserContext
from ipo_portal.sources import bse_nested_endpoints


class BseCurrentIssueFetchTests(unittest.TestCase):
    def test_cmn_rows_get_nested_subscription_endpoints(self) -> None:
        snapshots = [
            {
                "meta": {"source": "bse", "endpoint": "public_issue_details"},
                "body": {
                    "Table": [
                        {
                            "IR_flag": "CMN",
                            "IPO_NO": 7719,
                            "Scrip_cd": 4576,
                        }
                    ]
                },
            }
        ]

        endpoints = {endpoint.name for endpoint in bse_nested_endpoints(snapshots)}

        self.assertIn("issue_detail_7719", endpoints)
        self.assertIn("bid_details_7719", endpoints)
        self.assertIn("consolidated_bid_details_7719", endpoints)

    def test_bse_iso_midnight_dates_are_not_shifted_to_previous_utc_day(self) -> None:
        ctx = ParserContext(source="bse", endpoint="public_issue_details", snapshot_at="2026-05-26T04:50:21+00:00")
        out = bse_public_issue_details.parse(
            {
                "Table": [
                    {
                        "Scrip_Name": "PVV INFRA LTD",
                        "Start_Dt": "2026-05-15T00:00:00",
                        "End_Dt": "2026-05-29T00:00:00",
                        "Status": "L",
                        "IR_flag": "CMN",
                        "IPO_NO": 7719,
                    }
                ]
            },
            ctx,
        )

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].fields["timeline.open_date"], "2026-05-15")
        self.assertEqual(out[0].fields["timeline.close_date"], "2026-05-29")
        self.assertEqual(out[0].fields["identity.status"], "Open")
        self.assertEqual(out[0].fields["identity.issue_type"], "Call Money")


if __name__ == "__main__":
    unittest.main()
