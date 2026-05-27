from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from ipo_portal.site_builder import build_astro_site_data
from ipo_portal.trajectory import (
    extract_bse_observation,
    extract_nse_observation,
    is_trajectory_frozen,
    issue_key_index,
    merge_observations,
    read_trajectory,
    update_trajectories,
)


def bse_snapshot(observed_at: str, qib_times: str, retail_times: str, total_times: str) -> dict:
    return {
        "meta": {
            "source": "bse",
            "endpoint": "consolidated_bid_details_new_7722",
            "fetched_at": observed_at,
            "url": "https://example.com",
        },
        "body": {
            "table1": [
                {"SRNo": "Sr.No.", "col2": "Category", "col3": "Offered", "col4": "Bid", "col5": "Times"},
                {"SRNo": "1", "col2": "Qualified Institutional Buyers (QIBs)", "col3": "54000", "col4": "54000", "col5": qib_times},
                {"SRNo": "2.1", "col2": "NII >10L", "col3": "826800", "col4": "195600", "col5": "0.2366"},
                {"SRNo": "3", "col2": "Individual Investors", "col3": "1240800", "col4": "271200", "col5": retail_times},
                {"SRNo": "4", "col2": "Employee", "col3": "0", "col4": "0", "col5": ""},
                {"SRNo": "", "col2": "Total", "col3": "2670000", "col4": "549600", "col5": total_times},
            ]
        },
    }


def nse_snapshot(observed_at: str, total_times: str) -> dict:
    return {
        "meta": {
            "source": "nse",
            "endpoint": "consolidated_bid_details_bmll",
            "fetched_at": observed_at,
            "url": "https://example.com",
        },
        "body": {
            "dataList": [
                {"category": "Category", "noOfShareOffered": "offered", "noOfSharesBid": "bid", "noOfTotalMeant": "times", "srNo": "Sr.No."},
                {"category": "Qualified Institutional Buyers", "noOfShareOffered": "100", "noOfSharesBid": "150", "noOfTotalMeant": "1.5", "srNo": "1"},
                {"category": "Retail Individual Investors (RIIs)", "noOfShareOffered": "200", "noOfSharesBid": "100", "noOfTotalMeant": "0.5", "srNo": "3"},
                {"category": "Total", "noOfShareOffered": "300", "noOfSharesBid": "250", "noOfTotalMeant": total_times, "srNo": None},
            ],
            "updateTime": "Updated as on 22-May-2026 1:34 PM",
        },
    }


class TrajectoryParserTests(unittest.TestCase):
    def test_bse_parses_category_subscription(self) -> None:
        observation = extract_bse_observation(bse_snapshot("2026-05-22T07:33:17+00:00", "1.0000", "0.2186", "0.2058"))
        self.assertIsNotNone(observation)
        self.assertEqual(observation["source"], "bse")
        self.assertAlmostEqual(observation["categories"]["qib"]["times"], 1.0)
        self.assertAlmostEqual(observation["categories"]["retail"]["times"], 0.2186)
        self.assertAlmostEqual(observation["categories"]["nii_gt_10l"]["times"], 0.2366)
        self.assertAlmostEqual(observation["total"]["times"], 0.2058)
        self.assertEqual(observation["total"]["shares_offered"], 2670000)

    def test_bse_returns_none_when_all_zero(self) -> None:
        empty = {
            "meta": {"source": "bse", "endpoint": "consolidated_bid_details_new_7722", "fetched_at": "2026-05-22T07:33:17+00:00", "url": "x"},
            "body": {
                "table1": [
                    {"SRNo": "Sr.No.", "col2": "Category", "col3": "Offered", "col4": "Bid", "col5": "Times"},
                    {"SRNo": "1", "col2": "QIBs", "col3": "0", "col4": "0", "col5": "0"},
                    {"SRNo": "3", "col2": "Retail", "col3": "0", "col4": "0", "col5": "0"},
                    {"SRNo": "", "col2": "Total", "col3": "0", "col4": "0", "col5": "0"},
                ]
            },
        }
        self.assertIsNone(extract_bse_observation(empty))

    def test_nse_parses_total_only(self) -> None:
        observation = extract_nse_observation(nse_snapshot("2026-05-22T07:33:17+00:00", "0.75"))
        self.assertIsNotNone(observation)
        self.assertEqual(observation["source"], "nse")
        self.assertAlmostEqual(observation["categories"]["qib"]["times"], 1.5)
        self.assertAlmostEqual(observation["total"]["times"], 0.75)

    def test_nse_returns_none_when_all_zero(self) -> None:
        zero_snapshot = {
            "meta": {"source": "nse", "endpoint": "consolidated_bid_details_qline", "fetched_at": "2026-05-22T07:33:17+00:00", "url": "x"},
            "body": {
                "dataList": [
                    {"category": "Category", "srNo": "Sr.No."},
                    {"category": "Total", "noOfShareOffered": "0", "noOfSharesBid": "0", "noOfTotalMeant": "0", "srNo": None},
                ],
                "updateTime": "Updated as on null",
            },
        }
        self.assertIsNone(extract_nse_observation(zero_snapshot))


class TrajectoryPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.site_root = Path(self.tmp.name)
        self.issues = [
            {
                "slug": "harikanta-overseas-limited-ipo-2026-05-20-370976f8",
                "id": "7722-harikanta",
                "company": {"name": "Harikanta overseas Limited", "symbol": None},
                "sources": [{"source": "bse", "source_record_id": "7722"}],
            }
        ]
        self.key_index = issue_key_index(self.issues)
        self.lookup = {self.issues[0]["slug"]: self.issues[0]}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_persists_and_orders_observations(self) -> None:
        snap_a = bse_snapshot("2026-05-22T07:33:17+00:00", "1.0", "0.10", "0.15")
        snap_b = bse_snapshot("2026-05-22T08:05:05+00:00", "1.0", "0.22", "0.20")
        update_trajectories(self.site_root, [snap_b, snap_a], self.key_index, self.lookup)
        payload = read_trajectory(self.site_root, self.issues[0]["slug"])
        self.assertIsNotNone(payload)
        observed = [obs["observed_at"] for obs in payload["observations"]]
        self.assertEqual(observed, sorted(observed))
        self.assertEqual(len(payload["observations"]), 2)

    def test_idempotent_on_repeat(self) -> None:
        snap = bse_snapshot("2026-05-22T07:33:17+00:00", "1.0", "0.10", "0.15")
        update_trajectories(self.site_root, [snap], self.key_index, self.lookup)
        update_trajectories(self.site_root, [snap], self.key_index, self.lookup)
        payload = read_trajectory(self.site_root, self.issues[0]["slug"])
        self.assertEqual(len(payload["observations"]), 1)

    def test_freeze_skips_long_closed_issues(self) -> None:
        snap = bse_snapshot("2026-01-15T10:00:00+00:00", "1.0", "0.5", "0.4")
        closed_long_ago = [{
            "slug": "harikanta-overseas-limited-ipo-2026-05-20-370976f8",
            "id": "7722-old",
            "company": {"name": "Old Co"},
            "sources": [{"source": "bse", "source_record_id": "7722"}],
            "timeline": {"close_date": "2026-01-10"},
        }]
        key_index = issue_key_index(closed_long_ago)
        lookup = {closed_long_ago[0]["slug"]: closed_long_ago[0]}
        result = update_trajectories(self.site_root, [snap], key_index, lookup, as_of=date(2026, 5, 22))
        self.assertEqual(result, {})
        self.assertIsNone(read_trajectory(self.site_root, closed_long_ago[0]["slug"]))

    def test_freeze_respects_grace_period(self) -> None:
        snap = bse_snapshot("2026-05-22T10:00:00+00:00", "1.0", "0.5", "0.4")
        recently_closed = [{
            "slug": "harikanta-overseas-limited-ipo-2026-05-20-370976f8",
            "id": "7722-recent",
            "company": {"name": "Recent Co"},
            "sources": [{"source": "bse", "source_record_id": "7722"}],
            "timeline": {"close_date": "2026-05-20"},
        }]
        key_index = issue_key_index(recently_closed)
        lookup = {recently_closed[0]["slug"]: recently_closed[0]}
        result = update_trajectories(self.site_root, [snap], key_index, lookup, as_of=date(2026, 5, 22))
        self.assertEqual(len(result), 1)
        self.assertFalse(is_trajectory_frozen(recently_closed[0], date(2026, 5, 22)))
        self.assertTrue(is_trajectory_frozen(recently_closed[0], date(2026, 6, 1)))

    def test_merge_observations_dedupes_by_source_and_time(self) -> None:
        existing = [{"source": "bse", "observed_at": "2026-05-22T07:00:00+00:00", "categories": {}, "total": {"times": 0.1}}]
        incoming = [{"source": "bse", "observed_at": "2026-05-22T07:00:00+00:00", "categories": {}, "total": {"times": 0.2}}]
        merged = merge_observations(existing, incoming)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["total"]["times"], 0.2)


class TrajectoryAttachmentTests(unittest.TestCase):
    def test_site_builder_embeds_trajectory_in_by_slug(self) -> None:
        records = [
            {
                "id": "7722-record",
                "company_name": "Harikanta overseas Limited",
                "exchange_platform": "BSE",
                "issue_start_date": "2026-05-20",
                "issue_end_date": "2026-05-22",
                "issue_type": "IPO",
                "security_type": "Equity",
                "status": "active",
                "symbol": None,
                "documents": [],
                "sources": [
                    {"source": "bse", "source_record_id": "7722", "record_id": "7722-record", "endpoint": "public_issue", "observed_at": "2026-05-22T08:05:00+00:00"}
                ],
            }
        ]
        snapshots = [
            bse_snapshot("2026-05-22T07:33:17+00:00", "1.0", "0.10", "0.15"),
            bse_snapshot("2026-05-22T08:05:05+00:00", "1.0", "0.22", "0.20"),
        ]
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = build_astro_site_data(root, records, {"errors": [], "warnings": []}, date(2026, 5, 22), snapshots=snapshots)
            by_slug = list((root / "issues" / "by-slug").glob("*.json"))
            self.assertEqual(len(by_slug), 1)
            issue = json.loads(by_slug[0].read_text(encoding="utf-8"))
            trajectory = issue["subscription"]["trajectory"]
            self.assertEqual(len(trajectory), 2)
            self.assertEqual([obs["observed_at"] for obs in trajectory], sorted(obs["observed_at"] for obs in trajectory))
            self.assertEqual(manifest["counts"]["issues_with_subscription_trajectory"], 1)
            traj_files = list((root / "trajectories").glob("*.json"))
            self.assertEqual(len(traj_files), 1)

    def test_as_of_filters_future_observations(self) -> None:
        records = [
            {
                "id": "7722-record",
                "company_name": "Harikanta overseas Limited",
                "exchange_platform": "BSE",
                "issue_start_date": "2026-05-20",
                "issue_end_date": "2026-05-22",
                "issue_type": "IPO",
                "security_type": "Equity",
                "status": "active",
                "symbol": None,
                "documents": [],
                "sources": [
                    {"source": "bse", "source_record_id": "7722", "record_id": "7722-record", "endpoint": "public_issue", "observed_at": "2026-05-22T08:05:00+00:00"}
                ],
            }
        ]
        snapshots = [
            bse_snapshot("2026-05-21T07:33:17+00:00", "1.0", "0.10", "0.15"),
            bse_snapshot("2026-05-22T08:05:05+00:00", "1.0", "0.22", "0.20"),
        ]
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_astro_site_data(root, records, {"errors": [], "warnings": []}, date(2026, 5, 21), snapshots=snapshots)
            by_slug = list((root / "issues" / "by-slug").glob("*.json"))
            issue = json.loads(by_slug[0].read_text(encoding="utf-8"))
            visible = issue["subscription"]["trajectory"]
            self.assertEqual(len(visible), 1)
            self.assertTrue(visible[0]["observed_at"].startswith("2026-05-21"))
            self.assertTrue(any(r.get("field") == "subscription.trajectory" for r in issue.get("redactions", [])))


if __name__ == "__main__":
    unittest.main()
