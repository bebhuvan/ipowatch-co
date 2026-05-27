"""Tests for cross-key consolidation and trajectory slug resolution."""

from __future__ import annotations

import unittest

from ipo_portal.normalize_v2.pipeline import (
    Contribution,
    IssueJoinKey,
    _consolidate,
    _deterministic_build_at,
    _recompute_gains,
    _values_by_source,
    _year_from_join_key,
)
from ipo_portal.normalize_v2.parsers.bse_issue_detail import parse as parse_bse_issue_detail
from ipo_portal.normalize_v2.parsers.nse_ipo_past import _row_to_contribution as parse_nse_past_row
from ipo_portal.normalize_v2.parsers.nse_offer_documents import _row_to_contribution as parse_nse_offer_doc_row
from ipo_portal.normalize_v2.parsers.registry import ParserContext
from ipo_portal.normalize_v2.trajectory_v2 import resolve_slug


def _contrib(disc: str, val: str, fields: dict) -> Contribution:
    return Contribution(
        source="test",
        endpoint="test_ep",
        snapshot_at="2026-01-01T00:00:00+00:00",
        join_key=IssueJoinKey(discriminator=disc, value=val),
        fields=fields,
    )


class ConsolidationTests(unittest.TestCase):
    def test_document_only_absorbs_into_dated_within_2y(self) -> None:
        # Dated record (live IPO, opened 2026) + document-only (DRHP, 2025).
        dated = _contrib(
            "name_year", "bio medica laboratories:2026",
            {"identity.company_name": "Bio Medica Laboratories Limited",
             "timeline.open_date": "2026-05-21"},
        )
        doc_only = _contrib(
            "name_year", "bio medica laboratories:2025",
            {"identity.company_name": "Bio Medica Laboratories Limited",
             "documents.drhp_url": "http://x/drhp.pdf"},
        )
        groups = _consolidate([dated, doc_only])
        # Both contributions should land in ONE cluster.
        self.assertEqual(len(groups), 1)
        merged = next(iter(groups.values()))
        self.assertEqual(len(merged), 2)

    def test_isin_unions_across_keys(self) -> None:
        a = _contrib("name_year", "acme:2024", {"identity.isin": "INE001A01036", "timeline.open_date": "2024-01-01"})
        b = _contrib("isin", "INE001A01036", {"identity.isin": "INE001A01036", "documents.rhp_url": "http://x"})
        groups = _consolidate([a, b])
        self.assertEqual(len(groups), 1)

    def test_two_dated_issues_different_type_not_merged(self) -> None:
        # IPO 2018 and a distinct FPO 2024 of the same company — different
        # issue_type, so different (name,type) clusters — must NOT merge.
        ipo = _contrib("name_year", "xyz:2018", {"identity.company_name": "XYZ Limited", "identity.issue_type": "IPO", "timeline.open_date": "2018-03-01"})
        fpo = _contrib("name_year", "xyz:2024", {"identity.company_name": "XYZ Limited", "identity.issue_type": "FPO", "timeline.open_date": "2024-03-01"})
        groups = _consolidate([ipo, fpo])
        self.assertEqual(len(groups), 2)

    def test_two_dated_same_type_distinct_eras_not_merged(self) -> None:
        # Two OFS by the same company >2y apart are distinct events — keep
        # them separate (both dated, same type, eras 2018 vs 2024).
        a = _contrib("name_year", "xyz:2018", {"identity.company_name": "XYZ Limited", "identity.issue_type": "OFS", "timeline.open_date": "2018-03-01"})
        b = _contrib("name_year", "xyz:2024", {"identity.company_name": "XYZ Limited", "identity.issue_type": "OFS", "timeline.open_date": "2024-03-01"})
        groups = _consolidate([a, b])
        self.assertEqual(len(groups), 2)

    def test_drhp_shadow_absorbs_into_single_dated_issue(self) -> None:
        # A DRHP filed years before the listing is the SAME IPO journey — it
        # must absorb into the single dated issue, not linger as a duplicate.
        dated = _contrib("name_year", "acme:2024", {"identity.company_name": "Acme Ltd", "identity.issue_type": "IPO", "timeline.open_date": "2024-01-01"})
        drhp = _contrib("name_year", "acme:2021", {"identity.company_name": "Acme Ltd", "identity.issue_type": "IPO", "documents.drhp_url": "http://x"})
        groups = _consolidate([dated, drhp])
        self.assertEqual(len(groups), 1)

    def test_ruchi_soya_fpo_hint_prevents_ipo_overmerge(self) -> None:
        # BSE labels the security type "Equity" but the detail row's own
        # remarks identify IPO_NO 5699 as the 2022 FPO. It must not be typed
        # as IPO, otherwise it can merge with the historical 2003 IPO/listing.
        body = {
            "IPONO_0": [
                {
                    "IPO_NO": "5699",
                    "ScripName": "RUCHI SOYA INDUSTRIES LTD",
                    "Security_Type": "Equity",
                    "Symbol": "RUCHISOYA",
                    "Issue_Period": "24 Mar 2022 to 30 Mar 2022",
                    "Price_Band": "615.00-650.00",
                    "Remarks": "FPO of Ruchi Soya Industries Limited",
                }
            ]
        }
        ctx = ParserContext("bse", "issue_detail_5699", "2026-05-23T15:31:11+00:00")
        [contribution] = list(parse_bse_issue_detail(body, ctx))
        self.assertEqual(contribution.fields["identity.issue_type"], "FPO")

        historical_ipo = _contrib(
            "name_year",
            "ruchi soya:2003",
            {
                "identity.company_name": "Ruchi Soya Industries Limited",
                "identity.issue_type": "IPO",
                "identity.symbol": "RUCHISOYA",
                "timeline.listing_date": "2003-01-02",
            },
        )
        groups = _consolidate([historical_ipo, contribution])
        self.assertEqual(len(groups), 2)

    def test_nse_past_drops_stale_listing_date_and_out_of_band_price(self) -> None:
        row = {
            "company": "Ruchi Soya Industries Limited",
            "ipoEndDate": "28-MAR-2022",
            "ipoStartDate": "24-MAR-2022",
            "issuePrice": "34",
            "listingDate": "02-JAN-2003",
            "priceRange": "Rs.615 to Rs.650",
            "securityType": "EQ",
            "symbol": "RUCHISOYA",
        }
        ctx = ParserContext("nse", "ipo_public_past_issues", "2026-05-22T09:28:24+00:00")
        contribution = parse_nse_past_row(row, ctx)
        self.assertIsNotNone(contribution)
        assert contribution is not None
        self.assertEqual(contribution.fields["identity.issue_type"], "FPO")
        self.assertNotIn("timeline.listing_date", contribution.fields)
        self.assertNotIn("pricing.issue_price_paise", contribution.fields)

    def test_timed_nse_row_wins_issue_type_over_document_hint(self) -> None:
        row = {
            "company": "Ruchi Soya Industries Limited",
            "drhpAttach": "https://nsearchives.nseindia.com/corporate/d_RUCHISOYA.zip",
            "drhpDate": "12-Jun-2021",
            "fpAttach": "https://nsearchives.nseindia.com/corporate/FP_RUCHISOYA_31MAR2023.pdf",
            "fpDate": "31-Mar-2022",
            "symbol": "-",
        }
        ctx = ParserContext("nse", "offer_documents_equity", "2026-05-22T09:28:24+00:00")
        contribution = parse_nse_offer_doc_row(row, ctx, board_type="Main Board")
        self.assertIsNotNone(contribution)
        assert contribution is not None
        self.assertEqual(contribution.fields["identity.issue_type"], "IPO")
        timed = Contribution(
            "nse",
            "ipo_public_past_issues",
            "2026-05-22T09:28:24+00:00",
            contribution.join_key,
            {"identity.issue_type": "FPO", "timeline.open_date": "2022-03-24"},
        )
        self.assertEqual(_values_by_source([contribution, timed], "identity.issue_type")["nse"], "FPO")


class YearFromKeyTests(unittest.TestCase):
    def test_name_year(self) -> None:
        self.assertEqual(_year_from_join_key(IssueJoinKey("name_year", "acme:2024")), 2024)

    def test_isin_has_no_year(self) -> None:
        self.assertIsNone(_year_from_join_key(IssueJoinKey("isin", "INE001A01036")))

    def test_deterministic_build_at_uses_latest_snapshot(self) -> None:
        contribs = [
            _contrib("name_year", "a:2024", {}),
            Contribution("bse", "x", "2026-05-23T15:31:11+00:00", IssueJoinKey("name_year", "b:2026"), {}),
            Contribution("nse", "y", "2026-05-22T09:28:24+00:00", IssueJoinKey("name_year", "c:2026"), {}),
        ]
        self.assertEqual(_deterministic_build_at(contribs), "2026-05-23T15:31:11+00:00")

    def test_recompute_gains_drops_zero_current_price_and_stale_gain(self) -> None:
        record = {
            "pricing": {"issue_price_paise": 36000},
            "listing_performance": {"current_price_paise": 0, "current_gain_bps": 3000},
        }
        _recompute_gains(record)
        self.assertNotIn("current_price_paise", record["listing_performance"])
        self.assertNotIn("current_gain_bps", record["listing_performance"])


class TrajectoryResolveTests(unittest.TestCase):
    def test_bse_ipo_no(self) -> None:
        by_ipo = {"7722": "vegorama-x"}
        self.assertEqual(
            resolve_slug("bse", "consolidated_bid_details_new_7722", by_ipo, {}),
            "vegorama-x",
        )

    def test_nse_symbol(self) -> None:
        by_sym = {"BMLL": "bio-medica-x"}
        self.assertEqual(
            resolve_slug("nse", "consolidated_bid_details_bmll", {}, by_sym),
            "bio-medica-x",
        )

    def test_unmapped_returns_none(self) -> None:
        self.assertIsNone(resolve_slug("bse", "consolidated_bid_details_new_9999", {}, {}))


if __name__ == "__main__":
    unittest.main()
