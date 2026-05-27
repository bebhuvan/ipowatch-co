"""Audit primary NSE/BSE/SEBI source coverage for the V3 public contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_SITE_ROOT = PROJECT_ROOT / "data" / "ipo_watch_v3"
DEFAULT_REPORT = PROJECT_ROOT / "data" / "reports" / "primary_source_structure_audit.json"

PRIMARY_FAILURE_EXEMPT_PREFIXES = (
    # HTML graph endpoints are optional visual companions; structured demand
    # schedules and bid-detail APIs are the canonical data source.
    "demand_graph_",
)

OUT_OF_SCOPE_ENDPOINTS = {
    # Product decision: IPOWatch V3 covers company/security issuance actions,
    # not discontinued sovereign products or NSE non-company primary surfaces.
    "sgb_live_issues",
    "zczp_active",
    "zczp_company_list",
    "zczp_forthcoming",
    "zczp_past",
}

OUT_OF_SCOPE_PREFIXES = (
    "lwf",
    "mfss",
    "ncbgsec",
    "noncompbid",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit NSE/BSE/SEBI primary source structure coverage.")
    parser.add_argument("--site-root", type=Path, default=DEFAULT_SITE_ROOT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--gate", action="store_true", help="Exit non-zero when primary source coverage has blocking gaps.")
    args = parser.parse_args()

    coverage_path = args.site_root / "_meta" / "source_coverage.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    endpoints = coverage.get("endpoints") or []

    primary_rows = [
        row for row in endpoints
        if row.get("source") in {"nse", "bse", "sebi", "yahoo", "kite"}
    ]
    blocking = [
        row for row in primary_rows
        if row.get("classification") in {"fetch failed", "parser failed", "unclassified"}
        and not _is_exempt(row)
    ]
    unsupported_primary = [
        row for row in primary_rows
        if row.get("classification") == "unsupported gap"
    ]
    bid_or_demand_rows = [
        row for row in primary_rows
        if str(row.get("endpoint") or "").startswith(("demand_data_", "consolidated_bid_details_", "bid_details_"))
    ]
    nse_demand_rows = [
        row for row in primary_rows
        if row.get("source") == "nse" and str(row.get("endpoint") or "").startswith("demand_data_")
    ]

    report = {
        "site_root": str(args.site_root),
        "coverage_path": str(coverage_path),
        "summary": coverage.get("summary") or {},
        "primary_endpoint_count": len(primary_rows),
        "blocking_count": len(blocking),
        "unsupported_primary_count": len(unsupported_primary),
        "nse_demand_endpoint_count": len(nse_demand_rows),
        "bse_bid_endpoint_count": sum(1 for row in bid_or_demand_rows if row.get("source") == "bse"),
        "blocking": blocking,
        "unsupported_primary": unsupported_primary,
        "demand_endpoints_sample": bid_or_demand_rows[:50],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "primary_endpoint_count": report["primary_endpoint_count"],
        "blocking_count": report["blocking_count"],
        "unsupported_primary_count": report["unsupported_primary_count"],
        "nse_demand_endpoint_count": report["nse_demand_endpoint_count"],
        "bse_bid_endpoint_count": report["bse_bid_endpoint_count"],
        "report": str(args.report),
    }, sort_keys=True))
    return 1 if args.gate and (blocking or unsupported_primary) else 0


def _is_exempt(row: dict[str, object]) -> bool:
    endpoint = str(row.get("endpoint") or "")
    return (
        endpoint in OUT_OF_SCOPE_ENDPOINTS
        or endpoint.startswith(PRIMARY_FAILURE_EXEMPT_PREFIXES)
        or endpoint.startswith(OUT_OF_SCOPE_PREFIXES)
    )


if __name__ == "__main__":
    raise SystemExit(main())
