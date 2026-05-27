"""Audit gate for the IPO Watch V3 public dataset."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SITE_ROOT = ROOT / "data" / "ipo_watch_v3"
TODAY = date(2026, 5, 24)
GATE_CLASSES = {
    "file.by_slug_missing",
    "file.unparseable",
    "manifest.issues_count_mismatch",
    "manifest.companies_count_mismatch",
    "manifest.trajectories_count_mismatch",
    "index.quarantined_record_published",
    "source_coverage.unclassified_endpoint",
    "prospectus.unverified_fact_published",
    "market.current_price_nonpositive",
    "market.listing_gain_below_minus_100pct",
    "market.current_gain_below_minus_100pct",
    "pricing.issue_price_paise.nonpositive",
    "pricing.issue_price_paise.under_1rupee",
    "pricing.price_band_lower_paise.nonpositive",
    "pricing.price_band_upper_paise.nonpositive",
    "date.open_after_close",
    "date.close_after_listing",
    "identity.no_company_name",
    "identity.bad_isin",
}


def main(site_root: Path = DEFAULT_SITE_ROOT, gate: bool = False) -> int:
    findings: dict[str, list[str]] = defaultdict(list)
    by_slug = site_root / "issues" / "by-slug"
    if not by_slug.exists():
        findings["file.by_slug_missing"].append(str(by_slug))
        return _print(findings, 0, gate)

    records = []
    for path in sorted(by_slug.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            findings["file.unparseable"].append(path.name)
            continue
        records.append(doc)
        _audit_issue(doc, findings)

    _audit_manifest(site_root, len(records), findings)
    _audit_indexes(site_root, findings)
    _audit_source_coverage(site_root, findings)
    _audit_prospectus(site_root, findings)
    return _print(findings, len(records), gate)


def _audit_issue(doc: dict, findings: dict[str, list[str]]) -> None:
    slug = doc.get("slug") or "?"
    identity = doc.get("identity") or {}
    timeline = doc.get("timeline") or {}
    pricing = doc.get("pricing") or {}
    perf = doc.get("listing_performance") or {}
    if not identity.get("company_name"):
        findings["identity.no_company_name"].append(slug)
    isin = identity.get("isin")
    if isin and not (isinstance(isin, str) and len(isin) == 12 and isin.startswith("IN") and isin.isalnum() and isin.upper() == isin):
        findings["identity.bad_isin"].append(slug)
    od, cd, ld = _d(timeline.get("open_date")), _d(timeline.get("close_date")), _d(timeline.get("listing_date"))
    if od and cd and od > cd:
        findings["date.open_after_close"].append(slug)
    if cd and ld and cd > ld:
        findings["date.close_after_listing"].append(slug)
    for key in ("issue_price_paise", "price_band_lower_paise", "price_band_upper_paise"):
        value = pricing.get(key)
        if isinstance(value, int) and value <= 0:
            findings[f"pricing.{key}.nonpositive"].append(slug)
        if isinstance(value, int) and value < 100:
            findings[f"pricing.{key}.under_1rupee"].append(slug)
    if isinstance(perf.get("listing_gain_bps"), int) and perf["listing_gain_bps"] < -10000:
        findings["market.listing_gain_below_minus_100pct"].append(slug)
    if isinstance(perf.get("current_gain_bps"), int) and perf["current_gain_bps"] < -10000:
        findings["market.current_gain_below_minus_100pct"].append(slug)
    if isinstance(perf.get("current_price_paise"), int) and perf["current_price_paise"] <= 0:
        findings["market.current_price_nonpositive"].append(slug)


def _audit_manifest(site_root: Path, issue_count: int, findings: dict[str, list[str]]) -> None:
    manifest_path = site_root / "manifest.json"
    if not manifest_path.exists():
        findings["file.manifest_missing"].append(str(manifest_path))
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    companies = len(list((site_root / "companies" / "by-slug").glob("*.json")))
    trajectories = len(list((site_root / "trajectories").glob("*.json"))) if (site_root / "trajectories").exists() else 0
    if manifest.get("issues_published") != issue_count or manifest.get("issues_total") != issue_count:
        findings["manifest.issues_count_mismatch"].append(f"manifest={manifest.get('issues_published')} disk={issue_count}")
    if manifest.get("companies_total") != companies:
        findings["manifest.companies_count_mismatch"].append(f"manifest={manifest.get('companies_total')} disk={companies}")
    if manifest.get("trajectories_total") != trajectories:
        findings["manifest.trajectories_count_mismatch"].append(f"manifest={manifest.get('trajectories_total')} disk={trajectories}")


def _audit_indexes(site_root: Path, findings: dict[str, list[str]]) -> None:
    public = json.loads((site_root / "issues" / "index.json").read_text(encoding="utf-8"))
    quarantined = {p.stem for p in (site_root / "issues" / "quarantine").glob("*.json")} if (site_root / "issues" / "quarantine").exists() else set()
    for row in public.get("items") or []:
        if row.get("slug") in quarantined or row.get("data_quality_state") == "quarantined":
            findings["index.quarantined_record_published"].append(str(row.get("slug")))


def _audit_source_coverage(site_root: Path, findings: dict[str, list[str]]) -> None:
    path = site_root / "_meta" / "source_coverage.json"
    if not path.exists():
        findings["file.source_coverage_missing"].append(str(path))
        return
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("summary", {}).get("unclassified"):
        findings["source_coverage.unclassified_endpoint"].append(str(doc["summary"]["unclassified"]))


def _audit_prospectus(site_root: Path, findings: dict[str, list[str]]) -> None:
    for path in sorted((site_root / "issues").glob("*/prospectus_facts.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for key, value in _walk_scalars(doc.get("facts") or {}):
            if value is None:
                continue
            if not isinstance(value, dict):
                findings["prospectus.unverified_fact_published"].append(f"{path.parent.name}:{key}")
                continue
            required = {"value", "raw_excerpt", "source_page", "source_section", "confidence"}
            if value.get("value") is not None and not required.issubset(value):
                findings["prospectus.unverified_fact_published"].append(f"{path.parent.name}:{key}")


def _walk_scalars(value, prefix=""):
    if isinstance(value, dict):
        if "value" in value:
            yield prefix, value
        else:
            for key, child in value.items():
                yield from _walk_scalars(child, f"{prefix}.{key}".strip("."))
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            yield from _walk_scalars(child, f"{prefix}[{idx}]")
    else:
        yield prefix, value


def _d(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _print(findings: dict[str, list[str]], total: int, gate: bool) -> int:
    print(f"=== V3 quality audit: {total:,} public issue records ===")
    if not findings:
        print("No findings.")
        return 0
    for key in sorted(findings):
        print(f"{key}: {len(findings[key])}")
        for sample in findings[key][:5]:
            print(f"  {sample}")
    gate_hits = [key for key, values in findings.items() if values and key in GATE_CLASSES]
    if gate and gate_hits:
        print("GATE FAILED")
        return 1
    if gate:
        print("GATE PASSED")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-root", type=Path, default=DEFAULT_SITE_ROOT)
    parser.add_argument("--gate", action="store_true")
    args = parser.parse_args()
    raise SystemExit(main(site_root=args.site_root, gate=args.gate))
