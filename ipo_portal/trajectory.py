from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from .storage import write_json


TRAJECTORY_SCHEMA_VERSION = "1.0.0"
TRAJECTORY_FREEZE_GRACE_DAYS = 7


CATEGORY_KEYS = (
    "qib",
    "qib_fii",
    "qib_dfi",
    "qib_mf",
    "qib_other",
    "nii",
    "nii_gt_10l",
    "nii_lte_10l",
    "retail",
    "employee",
    "shareholder",
    "policyholder",
)


def parse_decimal(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "NA", "N/A"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def parse_int(value: Any) -> int | None:
    number = parse_decimal(value)
    return int(number) if number is not None else None


HEADER_SRNO_TOKENS = {"sr.no.", "sr no", "srno", "sr.no", "sr. no."}


def is_header_row(srno: Any, label: Any = None) -> bool:
    srno_text = str(srno or "").strip().lower()
    if srno_text in HEADER_SRNO_TOKENS:
        return True
    label_text = str(label or "").strip().lower()
    return not srno_text and label_text == "category"


def is_total_row(srno: Any, label: Any) -> bool:
    if str(srno or "").strip():
        return False
    return (str(label or "").strip().lower() == "total")


def bse_category_key(srno: str) -> str | None:
    """Map a BSE SRNo to our canonical category key. Returns None for rows we ignore."""
    key = str(srno or "").strip().lower()
    if key == "1":
        return "qib"
    if key == "1(a)":
        return "qib_fii"
    if key == "1(b)":
        return "qib_dfi"
    if key == "1(c)":
        return "qib_mf"
    if key == "1(d)":
        return "qib_other"
    if key == "2":
        return "nii"
    if key == "2.1":
        return "nii_gt_10l"
    if key == "2.2":
        return "nii_lte_10l"
    if key == "3":
        return "retail"
    if key == "4":
        return "employee"
    if key == "5":
        return "shareholder"
    if key == "6":
        return "policyholder"
    return None


def nse_category_key(category_text: str) -> str | None:
    text = (category_text or "").strip().lower()
    if not text:
        return None
    if "qualified institutional" in text or text.startswith("qib"):
        return "qib"
    if "non institutional" in text or text.startswith("nii"):
        if ">" in text or "more than" in text or "above" in text:
            return "nii_gt_10l"
        if "<=" in text or "upto" in text or "up to" in text:
            return "nii_lte_10l"
        return "nii"
    if "retail" in text or "rii" in text or "individual investors" in text:
        return "retail"
    if "employee" in text:
        return "employee"
    if "shareholder" in text or "shareholders" in text:
        return "shareholder"
    if "policyholder" in text:
        return "policyholder"
    return None


def category_payload(shares_offered: Any, shares_bid: Any, times: Any) -> dict[str, Any]:
    return {
        "times": parse_decimal(times),
        "shares_offered": parse_int(shares_offered),
        "shares_bid": parse_int(shares_bid),
    }


def empty_observation_payload(observation: dict[str, Any]) -> bool:
    if observation["total"].get("times"):
        return False
    for value in observation["categories"].values():
        if value.get("times"):
            return False
    return True


def extract_bse_observation(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a BSE consolidated_bid_details_new raw snapshot into one observation."""
    meta = snapshot.get("meta", {})
    body = snapshot.get("body")
    if not isinstance(body, dict):
        return None
    rows = body.get("table1")
    if not isinstance(rows, list):
        return None

    observed_at = meta.get("fetched_at")
    if not observed_at:
        return None

    categories: dict[str, dict[str, Any]] = {}
    total = {"times": None, "shares_offered": None, "shares_bid": None}
    source_updated_at: str | None = None

    for row in rows:
        if not isinstance(row, dict):
            continue
        srno = row.get("SRNo")
        label = row.get("col2")
        if is_header_row(srno, label):
            continue
        shares_offered = row.get("col3")
        shares_bid = row.get("col4")
        times = row.get("col5")
        maxdt = row.get("Maxdt")
        if maxdt:
            maxdt_text = str(maxdt).strip()
            if maxdt_text and maxdt_text.lower() != "none":
                source_updated_at = source_updated_at or maxdt_text

        if is_total_row(srno, label):
            total = category_payload(shares_offered, shares_bid, times)
            continue
        key = bse_category_key(srno)
        if not key:
            continue
        categories[key] = category_payload(shares_offered, shares_bid, times)

    observation = {
        "observed_at": observed_at,
        "source": "bse",
        "source_updated_at": source_updated_at,
        "categories": categories,
        "total": total,
    }
    if empty_observation_payload(observation):
        return None
    return observation


def extract_nse_observation(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Parse an NSE consolidated_bid_details (ipo-active-category) raw snapshot."""
    meta = snapshot.get("meta", {})
    body = snapshot.get("body")
    if not isinstance(body, dict):
        return None
    rows = body.get("dataList")
    if not isinstance(rows, list):
        return None

    observed_at = meta.get("fetched_at")
    if not observed_at:
        return None

    categories: dict[str, dict[str, Any]] = {}
    total = {"times": None, "shares_offered": None, "shares_bid": None}

    for row in rows:
        if not isinstance(row, dict):
            continue
        srno = row.get("srNo")
        label = row.get("category")
        if is_header_row(srno, label):
            continue
        shares_offered = row.get("noOfShareOffered")
        shares_bid = row.get("noOfSharesBid")
        times = row.get("noOfTotalMeant")
        if str(label or "").strip().lower() == "total":
            total = category_payload(shares_offered, shares_bid, times)
            continue
        key = nse_category_key(str(label or ""))
        if not key:
            continue
        categories[key] = category_payload(shares_offered, shares_bid, times)

    observation = {
        "observed_at": observed_at,
        "source": "nse",
        "source_updated_at": body.get("updateTime"),
        "categories": categories,
        "total": total,
    }
    if empty_observation_payload(observation):
        return None
    return observation


def trajectory_path(site_root: Path, issue_slug: str) -> Path:
    return site_root / "trajectories" / f"{issue_slug}.json"


def read_trajectory(site_root: Path, issue_slug: str) -> dict[str, Any] | None:
    path = trajectory_path(site_root, issue_slug)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def merge_observations(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge observations, deduping by (source, observed_at). Latest write wins per key."""
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for obs in existing:
        by_key[(obs.get("source", ""), obs.get("observed_at", ""))] = obs
    for obs in incoming:
        by_key[(obs.get("source", ""), obs.get("observed_at", ""))] = obs
    return sorted(by_key.values(), key=lambda item: (item.get("observed_at") or "", item.get("source") or ""))


def write_trajectory(site_root: Path, issue_slug: str, payload: dict[str, Any]) -> None:
    write_json(trajectory_path(site_root, issue_slug), payload)


def issue_key_index(issues: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """Build a {(source, key): issue_slug} index for routing snapshots to issues.

    Keys are lowercase for case-insensitive matching against snapshot endpoint suffixes.
    For BSE: key is the IPO_NO. For NSE: key is the trading symbol.
    """
    index: dict[tuple[str, str], str] = {}
    for issue in issues:
        slug = issue.get("slug")
        if not slug:
            continue
        for source in issue.get("sources", []):
            source_name = source.get("source")
            source_record_id = source.get("source_record_id")
            if not source_name or not source_record_id:
                continue
            index[(source_name, str(source_record_id).lower())] = slug
        symbol = (issue.get("company") or {}).get("symbol")
        if symbol:
            index[("nse", str(symbol).lower())] = slug
    return index


SNAPSHOT_ENDPOINT_PREFIXES = {
    "bse": ("consolidated_bid_details_new_",),
    "nse": ("consolidated_bid_details_",),
}


def snapshot_routing_key(snapshot: dict[str, Any]) -> tuple[str, str] | None:
    meta = snapshot.get("meta", {})
    source = meta.get("source")
    endpoint = meta.get("endpoint", "") or ""
    if not source or source not in SNAPSHOT_ENDPOINT_PREFIXES:
        return None
    for prefix in SNAPSHOT_ENDPOINT_PREFIXES[source]:
        if endpoint.startswith(prefix):
            return (source, endpoint[len(prefix):].lower())
    return None


def extract_observation(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    source = snapshot.get("meta", {}).get("source")
    if source == "bse":
        return extract_bse_observation(snapshot)
    if source == "nse":
        return extract_nse_observation(snapshot)
    return None


def discover_trajectory_snapshots(data_root: Path) -> list[dict[str, Any]]:
    """Walk data/raw/<source>/<bid-detail-endpoint>/*.json for every historical snapshot.

    We need all historical snapshots (not just the latest) so a first-run build
    captures every observation still on disk; subsequent merges are idempotent
    via merge_observations.
    """
    snapshots: list[dict[str, Any]] = []
    raw_root = data_root / "raw"
    if not raw_root.exists():
        return snapshots
    for source, prefixes in SNAPSHOT_ENDPOINT_PREFIXES.items():
        source_dir = raw_root / source
        if not source_dir.exists():
            continue
        for endpoint_dir in sorted(source_dir.iterdir()):
            if not endpoint_dir.is_dir():
                continue
            if not any(endpoint_dir.name.startswith(prefix) for prefix in prefixes):
                continue
            for snap_path in sorted(endpoint_dir.glob("*.json")):
                try:
                    snapshots.append(json.loads(snap_path.read_text(encoding="utf-8")))
                except json.JSONDecodeError:
                    continue
    return snapshots


def is_trajectory_frozen(issue: dict[str, Any], as_of: date) -> bool:
    """A trajectory is frozen once the IPO has been closed long enough that no new
    bid data will arrive. We refuse to read or write frozen trajectories so that
    builds touch only currently active issues no matter how big history grows.
    """
    timeline = issue.get("timeline") or {}
    close_text = timeline.get("close_date")
    if not close_text:
        return False
    try:
        close_date = date.fromisoformat(close_text)
    except (TypeError, ValueError):
        return False
    return close_date < (as_of - timedelta(days=TRAJECTORY_FREEZE_GRACE_DAYS))


def update_trajectories(
    site_root: Path,
    snapshots: Iterable[dict[str, Any]],
    key_index: dict[tuple[str, str], str],
    issue_lookup: dict[str, dict[str, Any]] | None = None,
    data_root: Path | None = None,
    as_of: date | None = None,
) -> dict[str, dict[str, Any]]:
    """Parse every relevant snapshot, route to an issue slug, and persist merged trajectories.

    Returns {issue_slug: trajectory_payload} for the issues that were updated.
    Skips issues whose trajectory is already frozen (close_date long past).
    """
    source_snapshots: list[dict[str, Any]] = list(snapshots)
    if data_root is not None:
        source_snapshots.extend(discover_trajectory_snapshots(data_root))

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for snapshot in source_snapshots:
        routing_key = snapshot_routing_key(snapshot)
        if not routing_key:
            continue
        slug = key_index.get(routing_key)
        if not slug:
            continue
        observation = extract_observation(snapshot)
        if not observation:
            continue
        grouped[slug].append(observation)

    result: dict[str, dict[str, Any]] = {}
    lookup = issue_lookup or {}
    for slug, incoming in grouped.items():
        issue_meta = lookup.get(slug) or {}
        if as_of and issue_meta and is_trajectory_frozen(issue_meta, as_of):
            continue
        existing_payload = read_trajectory(site_root, slug) or {}
        existing_obs = existing_payload.get("observations", []) if isinstance(existing_payload, dict) else []
        merged = merge_observations(existing_obs, incoming)
        payload = {
            "schema_version": TRAJECTORY_SCHEMA_VERSION,
            "issue_slug": slug,
            "issue_id": issue_meta.get("id") or existing_payload.get("issue_id"),
            "company_name": (issue_meta.get("company") or {}).get("name") or existing_payload.get("company_name"),
            "frozen_at": existing_payload.get("frozen_at"),
            "observations": merged,
        }
        write_trajectory(site_root, slug, payload)
        result[slug] = payload
    return result
