from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any

from .normalize import slugify
from .performance_export import export_performance_site_data
from .prime import build_prime_site_data
from .storage import write_json
from .trajectory import (
    issue_key_index,
    read_trajectory,
    update_trajectories,
)


SITE_SCHEMA_VERSION = "1.0.0"


def build_astro_site_data(
    site_root: Path,
    records: list[dict[str, Any]],
    validation_report: dict[str, Any],
    as_of: date,
    snapshots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    issue_quality = quality_by_issue_id(validation_report)
    auxiliary = auxiliary_by_key(snapshots or [])
    issues = [to_issue(record, issue_quality, auxiliary, site_root=None) for record in records]
    key_index = issue_key_index(issues)
    issue_lookup = {issue["slug"]: issue for issue in issues if issue.get("slug")}
    data_root = site_root.parent if site_root.parent != site_root else None
    update_trajectories(site_root, snapshots or [], key_index, issue_lookup, data_root=data_root, as_of=as_of)
    for issue in issues:
        attach_trajectory(issue, site_root, as_of)
    companies = build_companies(issues)
    indexes = build_indexes(issues, companies)
    prime = build_prime_site_data(site_root, snapshots or [])
    performance_summary = export_performance_site_data(site_root, issues)
    manifest = build_manifest(issues, companies, validation_report, as_of)
    manifest["counts"]["performance_export_rows"] = performance_summary["total_rows"]
    manifest["counts"]["performance_export_pages"] = performance_summary["page_count"]
    manifest["counts"]["kite_listing_prices"] = performance_summary["with_kite_listing"]
    manifest["counts"]["kite_current_prices"] = performance_summary["with_kite_current"]
    manifest["paths"]["performance_summary"] = "performance/summary.json"
    manifest["paths"]["performance_index"] = "performance/index.json"
    manifest["paths"]["performance_pages"] = "performance/pages/page-{n}.json"
    if prime["page_count"]:
        manifest["counts"]["prime_demo_pages"] = prime["page_count"]
        manifest["counts"]["prime_coverage_rows"] = prime["coverage_row_count"]
        manifest["paths"].update(prime["paths"])
    schema = build_schema()

    write_json(site_root / "manifest.json", manifest)
    write_json(site_root / "schema.json", schema)
    write_json(site_root / "issues" / "index.json", issues)
    write_json(site_root / "issues" / "current.json", indexes["issue_status"]["current"])
    write_json(site_root / "issues" / "upcoming.json", indexes["issue_status"]["upcoming"])
    write_json(site_root / "issues" / "historical.json", indexes["issue_status"]["historical"])
    write_json(site_root / "issues" / "documents.json", indexes["issue_status"]["documents"])
    write_json(site_root / "issues" / "ofs.json", indexes["issue_kinds"]["ofs"])
    write_json(site_root / "issues" / "tender.json", indexes["issue_kinds"]["tender"])
    write_json(site_root / "issues" / "buybacks.json", indexes["issue_kinds"]["buybacks"])
    write_json(site_root / "issues" / "rights.json", indexes["issue_kinds"]["rights"])
    write_json(site_root / "issues" / "ipp.json", indexes["issue_kinds"]["ipp"])
    write_json(site_root / "issues" / "invits.json", indexes["issue_kinds"]["invits"])
    write_json(site_root / "issues" / "reits.json", indexes["issue_kinds"]["reits"])
    write_json(site_root / "issues" / "zczp.json", indexes["issue_kinds"]["zczp"])
    write_json(site_root / "issues" / "performance.json", indexes["performance"])
    write_json(site_root / "companies" / "index.json", companies)
    write_json(site_root / "indexes.json", indexes)

    for issue in issues:
        write_json(site_root / "issues" / "by-slug" / f"{issue['slug']}.json", issue)

    for company in companies:
        write_json(site_root / "companies" / "by-slug" / f"{company['slug']}.json", company)

    for year, year_issues in indexes["issues_by_year"].items():
        write_json(site_root / "issues" / "by-year" / f"{year}.json", year_issues)

    return manifest


def to_issue(
    record: dict[str, Any],
    issue_quality: dict[str, dict[str, list[dict[str, Any]]]],
    auxiliary: dict[tuple[str, str], dict[str, Any]],
    site_root: Path | None = None,
) -> dict[str, Any]:
    company_name = record.get("company_name")
    company_slug = stable_slug("company", company_name or "unknown")
    issue_slug = issue_slug_for(record)
    source_record_ids = [source.get("record_id") for source in record.get("sources", []) if source.get("record_id")]
    quality = compact_quality(record, source_record_ids, issue_quality)
    exchange_details = exchange_details_for(record, auxiliary)
    documents = merged_documents(record.get("documents", []), exchange_documents(exchange_details))

    return {
        "schema_version": SITE_SCHEMA_VERSION,
        "id": record.get("id"),
        "slug": issue_slug,
        "url_path": f"/ipos/{issue_slug}/",
        "title": issue_title(record),
        "company": {
            "id": company_slug,
            "name": company_name,
            "slug": company_slug,
            "url_path": f"/companies/{company_slug}/",
            "symbol": record.get("symbol"),
        },
        "classification": {
            "status": canonical_status(record.get("status")),
            "issue_type": record.get("issue_type"),
            "security_type": record.get("security_type"),
            "exchange_platform": record.get("exchange_platform"),
        },
        "timeline": {
            "open_date": record.get("issue_start_date"),
            "close_date": record.get("issue_end_date"),
            "listing_date": record.get("listing_date"),
        },
        "pricing": {
            "price_band": {
                "min": record.get("price_band_low"),
                "max": record.get("price_band_high"),
                "text": record.get("price_band_text"),
            },
            "issue_price": record.get("issue_price"),
            "face_value": record.get("face_value"),
        },
        "issue_size": {
            "text": record.get("issue_size_text"),
            "shares_offered": record.get("shares_offered"),
        },
        "subscription": {
            "shares_bid": record.get("shares_bid"),
            "times": record.get("subscription_times"),
            "trajectory": [],
        },
        "listing_performance": {
            "listing_day_open": record.get("listing_day_open"),
            "listing_day_close": record.get("listing_day_close"),
            "listing_open_gain": record.get("listing_open_gain"),
            "listing_day_gain": record.get("listing_day_gain"),
            "current_price": record.get("current_price"),
            "current_gain_from_listing_open": record.get("current_gain_from_listing_open"),
            "gain_loss": record.get("gain_loss"),
            "stock_url": record.get("stock_url"),
        },
        "exchange_details": exchange_details,
        "documents": sorted(documents, key=lambda item: (item.get("type") or "", item.get("url") or "")),
        "data_quality": quality,
        "redactions": record.get("redactions", []),
        "sources": record.get("sources", []),
        "updated_at": newest_observed_at(record),
    }


def attach_trajectory(issue: dict[str, Any], site_root: Path, as_of: date) -> None:
    payload = read_trajectory(site_root, issue["slug"])
    if not payload:
        return
    cutoff = as_of.isoformat()
    visible: list[dict[str, Any]] = []
    redacted = 0
    for observation in payload.get("observations", []) or []:
        observed_at = observation.get("observed_at") or ""
        if observed_at[:10] > cutoff:
            redacted += 1
            continue
        visible.append(observation)
    issue.setdefault("subscription", {})["trajectory"] = visible
    if redacted:
        issue.setdefault("redactions", []).append(
            {"field": "subscription.trajectory", "reason": "after_as_of", "count": redacted}
        )


def issue_title(record: dict[str, Any]) -> str:
    company = record.get("company_name") or "Unknown company"
    issue_type = record.get("issue_type") or "IPO"
    year = (record.get("issue_start_date") or record.get("issue_end_date") or record.get("listing_date") or "")[:4]
    return f"{company} {issue_type} {year}".strip()


def issue_slug_for(record: dict[str, Any]) -> str:
    company = slugify(record.get("company_name") or "unknown-company")
    issue_type = slugify(record.get("issue_type") or "ipo")
    start = record.get("issue_start_date") or record.get("listing_date") or "undated"
    digest = sha1((record.get("id") or company).encode("utf-8")).hexdigest()[:8]
    parts = [company, issue_type, start, digest]
    return "-".join(slugify(part) for part in parts if slugify(part))


def stable_slug(prefix: str, value: str) -> str:
    base = slugify(value) or prefix
    digest = sha1(value.lower().encode("utf-8")).hexdigest()[:8]
    return f"{base}-{digest}"


def canonical_status(status: str | None) -> str:
    if status in {"active", "upcoming", "past", "document"}:
        return "current" if status == "active" else status
    return status or "unknown"


def newest_observed_at(record: dict[str, Any]) -> str | None:
    observed = [source.get("observed_at") for source in record.get("sources", []) if source.get("observed_at")]
    if not observed:
        return record.get("observed_at")
    return max(observed)


def quality_by_issue_id(validation_report: dict[str, Any]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"errors": [], "warnings": []})
    for kind in ("errors", "warnings"):
        for finding in validation_report.get(kind, []):
            ids = []
            if finding.get("id"):
                ids.append(finding["id"])
            ids.extend(finding.get("ids", []))
            for issue_id in ids:
                compact = {key: value for key, value in finding.items() if key not in {"raw"}}
                grouped[issue_id][kind].append(compact)
    return grouped


def compact_quality(
    record: dict[str, Any],
    source_record_ids: list[str],
    issue_quality: dict[str, dict[str, list[dict[str, Any]]]],
) -> dict[str, Any]:
    ids = [record.get("id"), *source_record_ids]
    errors = []
    warnings = []
    for issue_id in ids:
        if not issue_id:
            continue
        errors.extend(issue_quality.get(issue_id, {}).get("errors", []))
        warnings.extend(issue_quality.get(issue_id, {}).get("warnings", []))

    return {
        "state": "blocked" if errors else "review" if warnings else "clean",
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": dedupe_findings(errors),
        "warnings": dedupe_findings(warnings),
    }


def dedupe_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    deduped = []
    for finding in findings:
        key = repr(sorted(finding.items()))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped


def build_companies(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for issue in issues:
        company = issue["company"]
        entry = grouped.setdefault(
            company["slug"],
            {
                "schema_version": SITE_SCHEMA_VERSION,
                "id": company["id"],
                "slug": company["slug"],
                "url_path": company["url_path"],
                "name": company["name"],
                "symbols": [],
                "issue_ids": [],
                "issue_slugs": [],
                "latest_issue_date": None,
                "sources": [],
            },
        )
        if company.get("symbol") and company["symbol"] not in entry["symbols"]:
            entry["symbols"].append(company["symbol"])
        entry["issue_ids"].append(issue["id"])
        entry["issue_slugs"].append(issue["slug"])
        issue_date = issue["timeline"].get("open_date") or issue["timeline"].get("close_date")
        if issue_date and (not entry["latest_issue_date"] or issue_date > entry["latest_issue_date"]):
            entry["latest_issue_date"] = issue_date
        for source in issue.get("sources", []):
            source_key = (source.get("source"), source.get("endpoint"), source.get("source_record_id"))
            if source_key not in {(item.get("source"), item.get("endpoint"), item.get("source_record_id")) for item in entry["sources"]}:
                entry["sources"].append(source)

    return sorted(grouped.values(), key=lambda item: item["name"] or "")


def build_indexes(issues: list[dict[str, Any]], companies: list[dict[str, Any]]) -> dict[str, Any]:
    current = []
    upcoming = []
    historical = []
    documents = []
    by_year: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_exchange: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_issue_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ofs = []
    tender = []
    buybacks = []
    rights = []
    ipp = []
    invits = []
    reits = []
    zczp = []
    performance = []

    for issue in issues:
        summary = issue_summary(issue)
        status = issue["classification"].get("status")
        if status == "current":
            current.append(summary)
        elif status == "upcoming":
            upcoming.append(summary)
        elif status == "document":
            documents.append(summary)
        else:
            historical.append(summary)

        year = ((issue["timeline"].get("open_date") or issue["timeline"].get("close_date") or "undated")[:4] or "undated")
        by_year[year].append(summary)
        exchange = issue["classification"].get("exchange_platform") or "unknown"
        by_exchange[exchange].append(summary)
        issue_type = issue["classification"].get("issue_type") or "unknown"
        by_issue_type[issue_type].append(summary)
        listing_performance = issue.get("listing_performance") or {}
        if issue["timeline"].get("listing_date") or any(listing_performance.get(field) is not None for field in ("listing_day_open", "listing_day_close", "listing_open_gain", "listing_day_gain", "current_price", "current_gain_from_listing_open", "gain_loss")):
            performance.append(summary)
        issue_type_key = issue_type.lower()
        source_endpoints = {source.get("endpoint") or "" for source in issue.get("sources", [])}
        if issue_type_key == "ofs":
            ofs.append(summary)
        if issue_type_key == "tender" or "tender" in issue_type_key or any(endpoint.startswith("tender_") for endpoint in source_endpoints):
            tender.append(summary)
        if "buy" in issue_type_key and "back" in issue_type_key:
            buybacks.append(summary)
        if issue_type_key == "rights":
            rights.append(summary)
        if issue_type_key == "ipp":
            ipp.append(summary)
        if issue_type_key == "invit":
            invits.append(summary)
        if issue_type_key == "reit":
            reits.append(summary)
        if issue_type_key == "zczp":
            zczp.append(summary)

    return {
        "schema_version": SITE_SCHEMA_VERSION,
        "issue_status": {
            "current": sort_issue_summaries(current),
            "upcoming": sort_issue_summaries(upcoming),
            "historical": sort_issue_summaries(historical, reverse=True),
            "documents": sort_issue_summaries(documents, reverse=True),
        },
        "issues_by_year": {year: sort_issue_summaries(items, reverse=True) for year, items in sorted(by_year.items())},
        "issues_by_exchange": {key: sort_issue_summaries(items, reverse=True) for key, items in sorted(by_exchange.items())},
        "issues_by_type": {key: sort_issue_summaries(items, reverse=True) for key, items in sorted(by_issue_type.items())},
        "issue_kinds": {
            "ofs": sort_issue_summaries(ofs, reverse=True),
            "tender": sort_issue_summaries(tender, reverse=True),
            "buybacks": sort_issue_summaries(buybacks, reverse=True),
            "rights": sort_issue_summaries(rights, reverse=True),
            "ipp": sort_issue_summaries(ipp, reverse=True),
            "invits": sort_issue_summaries(invits, reverse=True),
            "reits": sort_issue_summaries(reits, reverse=True),
            "zczp": sort_issue_summaries(zczp, reverse=True),
        },
        "performance": sort_issue_summaries(performance, reverse=True),
        "companies": [
            {
                "id": company["id"],
                "slug": company["slug"],
                "name": company["name"],
                "url_path": company["url_path"],
                "issue_count": len(company["issue_ids"]),
                "latest_issue_date": company["latest_issue_date"],
            }
            for company in companies
        ],
    }


def issue_summary(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": issue["id"],
        "slug": issue["slug"],
        "url_path": issue["url_path"],
        "title": issue["title"],
        "company_name": issue["company"]["name"],
        "status": issue["classification"]["status"],
        "issue_type": issue["classification"]["issue_type"],
        "exchange_platform": issue["classification"]["exchange_platform"],
        "open_date": issue["timeline"]["open_date"],
        "close_date": issue["timeline"]["close_date"],
        "listing_date": issue["timeline"]["listing_date"],
        "price_band": issue["pricing"]["price_band"],
        "listing_day_open": issue["listing_performance"]["listing_day_open"],
        "listing_day_gain": issue["listing_performance"]["listing_day_gain"],
        "listing_day_close": issue["listing_performance"]["listing_day_close"],
        "current_price": issue["listing_performance"]["current_price"],
        "current_gain_from_listing_open": issue["listing_performance"]["current_gain_from_listing_open"],
        "gain_loss": issue["listing_performance"]["gain_loss"],
        "issue_price": issue["pricing"]["issue_price"],
        "quality_state": issue["data_quality"]["state"],
    }


def sort_issue_summaries(items: list[dict[str, Any]], reverse: bool = False) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (item.get("open_date") or item.get("close_date") or item.get("listing_date") or "0000", item.get("company_name") or ""),
        reverse=reverse,
    )


def build_manifest(issues: list[dict[str, Any]], companies: list[dict[str, Any]], validation_report: dict[str, Any], as_of: date) -> dict[str, Any]:
    status_counts = defaultdict(int)
    quality_counts = defaultdict(int)
    for issue in issues:
        status_counts[issue["classification"]["status"]] += 1
        quality_counts[issue["data_quality"]["state"]] += 1

    return {
        "schema_version": SITE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "as_of": as_of.isoformat(),
        "counts": {
            "issues": len(issues),
            "companies": len(companies),
            "issues_with_bid_details": sum(1 for issue in issues if has_exchange_detail(issue, "bid_details")),
            "issues_with_demand_data": sum(1 for issue in issues if has_exchange_detail(issue, "demand_data") or has_exchange_detail(issue, "demand_schedule")),
            "issues_with_offer_documents": sum(1 for issue in issues if has_exchange_detail(issue, "offer_documents")),
            "issues_with_public_ads": sum(1 for issue in issues if has_exchange_detail(issue, "public_issue_advertisements")),
            "ofs_issues": sum(1 for issue in issues if (issue["classification"].get("issue_type") or "").lower() == "ofs"),
            "tender_issues": sum(1 for issue in issues if is_tender_issue(issue)),
            "buyback_issues": sum(1 for issue in issues if "buy" in (issue["classification"].get("issue_type") or "").lower() and "back" in (issue["classification"].get("issue_type") or "").lower()),
            "rights_issues": sum(1 for issue in issues if (issue["classification"].get("issue_type") or "").lower() == "rights"),
            "ipp_issues": sum(1 for issue in issues if (issue["classification"].get("issue_type") or "").lower() == "ipp"),
            "invit_issues": sum(1 for issue in issues if (issue["classification"].get("issue_type") or "").lower() == "invit"),
            "reit_issues": sum(1 for issue in issues if (issue["classification"].get("issue_type") or "").lower() == "reit"),
            "zczp_issues": sum(1 for issue in issues if (issue["classification"].get("issue_type") or "").lower() == "zczp"),
            "performance_issues": sum(1 for issue in issues if issue["timeline"].get("listing_date") or any((issue.get("listing_performance") or {}).get(field) is not None for field in ("listing_day_open", "listing_day_close", "listing_open_gain", "listing_day_gain", "current_price", "current_gain_from_listing_open", "gain_loss"))),
            "issues_with_subscription_trajectory": sum(1 for issue in issues if (issue.get("subscription") or {}).get("trajectory")),
            "by_status": dict(sorted(status_counts.items())),
            "by_quality": dict(sorted(quality_counts.items())),
            "validation_errors": validation_report.get("error_count", 0),
            "validation_warnings": validation_report.get("warning_count", 0),
        },
        "paths": {
            "all_issues": "issues/index.json",
            "current_issues": "issues/current.json",
            "upcoming_issues": "issues/upcoming.json",
            "historical_issues": "issues/historical.json",
            "document_issues": "issues/documents.json",
            "ofs_issues": "issues/ofs.json",
            "tender_issues": "issues/tender.json",
            "buyback_issues": "issues/buybacks.json",
            "rights_issues": "issues/rights.json",
            "ipp_issues": "issues/ipp.json",
            "invit_issues": "issues/invits.json",
            "reit_issues": "issues/reits.json",
            "zczp_issues": "issues/zczp.json",
            "performance_issues": "issues/performance.json",
            "all_companies": "companies/index.json",
            "indexes": "indexes.json",
            "schema": "schema.json",
        },
    }


def has_exchange_detail(issue: dict[str, Any], detail_name: str) -> bool:
    exchange_details = issue.get("exchange_details", {})
    return any(details.get(detail_name) for details in exchange_details.values() if isinstance(details, dict))


def is_tender_issue(issue: dict[str, Any]) -> bool:
    issue_type = (issue["classification"].get("issue_type") or "").lower()
    if "tender" in issue_type:
        return True
    return any((source.get("endpoint") or "").startswith("tender_") for source in issue.get("sources", []))


def build_schema() -> dict[str, Any]:
    return {
        "schema_version": SITE_SCHEMA_VERSION,
        "issue": {
            "required": ["id", "slug", "url_path", "title", "company", "classification", "timeline", "pricing", "documents", "sources"],
            "status_values": ["current", "upcoming", "past", "document", "unknown"],
            "quality_states": ["clean", "review", "blocked"],
            "date_format": "YYYY-MM-DD",
        },
        "subscription_trajectory": {
            "observation_required": ["observed_at", "source", "categories", "total"],
            "category_keys": [
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
            ],
            "category_fields": ["times", "shares_offered", "shares_bid"],
            "total_fields": ["times", "shares_offered", "shares_bid"],
        },
        "company": {
            "required": ["id", "slug", "url_path", "name", "issue_ids", "issue_slugs"],
        },
    }


def auxiliary_by_key(snapshots: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    auxiliary: dict[tuple[str, str], dict[str, Any]] = defaultdict(dict)
    nse_pan_keys = nse_pan_auxiliary_keys(snapshots)
    for snapshot in snapshots:
        meta = snapshot.get("meta", {})
        source = meta.get("source")
        endpoint = meta.get("endpoint") or ""
        body = snapshot.get("body")
        observed_at = meta.get("fetched_at")
        url = meta.get("url")

        if source == "nse":
            parts = endpoint.split("_")
            symbol = parts[-1].upper() if parts else ""
            if endpoint.startswith("issue_detail_") and len(parts) >= 4:
                symbol = parts[-2].upper()
                auxiliary[(source, symbol)]["issue_detail"] = snapshot_payload(body, observed_at, url)
            elif endpoint.startswith("bid_details_") and len(parts) >= 4:
                symbol = parts[-2].upper()
                auxiliary[(source, symbol)]["bid_details"] = table_payload(body.get("data") if isinstance(body, dict) else body, observed_at, url)
            elif endpoint.startswith("consolidated_bid_details_"):
                symbol = endpoint.replace("consolidated_bid_details_", "").upper()
                rows = body.get("dataList") if isinstance(body, dict) else body
                auxiliary[(source, symbol)]["consolidated_bid_details"] = table_payload(rows, observed_at, url, update_time=body.get("updateTime") if isinstance(body, dict) else None)
            elif endpoint.startswith("demand_data_nse_"):
                symbol = endpoint.replace("demand_data_nse_", "").upper()
                auxiliary[(source, symbol)].setdefault("demand_data", {})["nse"] = table_payload(body, observed_at, url)
            elif endpoint.startswith("demand_data_all_"):
                symbol = endpoint.replace("demand_data_all_", "").upper()
                auxiliary[(source, symbol)].setdefault("demand_data", {})["all_exchanges"] = table_payload(body, observed_at, url)
            elif endpoint.startswith("ofs_"):
                rows = body.get("data") if isinstance(body, dict) else body
                if isinstance(rows, list):
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        for key in nse_market_action_keys(row):
                            entry = auxiliary[key].setdefault("ofs_details", {"observed_at": observed_at, "url": url, "row_count": 0, "rows": []})
                            append_table_rows(entry, [row])
                            entry["row_count"] = len(entry["rows"])
            elif endpoint.startswith("tender_"):
                rows = body.get("data") if isinstance(body, dict) else body
                if isinstance(rows, list):
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        for key in nse_market_action_keys(row):
                            entry = auxiliary[key].setdefault("tender_details", {"observed_at": observed_at, "url": url, "row_count": 0, "rows": []})
                            append_table_rows(entry, [row])
                            entry["row_count"] = len(entry["rows"])
                            bid_rows = ((row.get("bidDetails") or {}).get("data") if isinstance(row.get("bidDetails"), dict) else None)
                            if isinstance(bid_rows, list):
                                auxiliary[key]["tender_bid_details"] = table_payload(bid_rows, observed_at, url)
                            demand_rows = row.get("demand")
                            if isinstance(demand_rows, list):
                                auxiliary[key]["tender_demand"] = table_payload(demand_rows, observed_at, url)
            elif endpoint.startswith(("rights_", "ipp_", "invits_", "reits_", "zczp_", "lwf_")):
                rows = site_rows_from_body(body)
                detail_name = public_issue_detail_name(endpoint)
                for row in rows:
                    for key in nse_market_action_keys(row):
                        entry = auxiliary[key].setdefault(detail_name, {"observed_at": observed_at, "url": url, "row_count": 0, "rows": []})
                        append_table_rows(entry, [row])
                        entry["row_count"] = len(entry["rows"])
            elif endpoint in {"offer_documents_equity", "offer_documents_sme"} and isinstance(body, list):
                for row in body:
                    if not isinstance(row, dict):
                        continue
                    keys = nse_row_keys(row)
                    if not keys:
                        continue
                    payload = table_payload([row], observed_at, url)
                    payload["documents"] = nse_offer_documents(row)
                    payload["market"] = "sme" if endpoint.endswith("_sme") else "mainboard"
                    for key in keys:
                        entry = auxiliary[key].setdefault("offer_documents", {"observed_at": observed_at, "url": url, "row_count": 0, "rows": [], "documents": []})
                        append_table_rows(entry, payload["rows"])
                        append_documents(entry["documents"], payload["documents"])
                        entry["row_count"] = len(entry["rows"])
            elif endpoint == "public_issue_advertisements" and isinstance(body, list):
                for row in body:
                    if not isinstance(row, dict):
                        continue
                    keys = nse_public_ad_keys(row)
                    if not keys:
                        continue
                    payload = table_payload([row], observed_at, url)
                    payload["documents"] = nse_public_issue_ad_documents(row)
                    for key in keys:
                        entry = auxiliary[key].setdefault("public_issue_advertisements", {"observed_at": observed_at, "url": url, "row_count": 0, "rows": [], "documents": []})
                        append_table_rows(entry, payload["rows"])
                        append_documents(entry["documents"], payload["documents"])
                        entry["row_count"] = len(entry["rows"])
            elif endpoint.startswith("offer_document_detail_"):
                pan = endpoint.replace("offer_document_detail_", "").upper()
                for key in nse_pan_keys.get(pan, []):
                    auxiliary[key]["offer_document_detail"] = snapshot_payload(body, observed_at, url)
            elif endpoint.startswith("offer_abridged_"):
                parts = endpoint.split("_")
                if len(parts) >= 4:
                    pan = parts[-1].upper()
                    prospectus_type = "_".join(parts[2:-1]).upper()
                    for key in nse_pan_keys.get(pan, []):
                        auxiliary[key].setdefault("abridged_prospectus", {})[prospectus_type] = snapshot_payload(body, observed_at, url)

        if source == "bse":
            issue_id = endpoint.rsplit("_", 1)[-1]
            key = (source, issue_id)
            if endpoint.startswith("issue_detail_"):
                auxiliary[key]["issue_detail"] = snapshot_payload(body, observed_at, url)
            elif endpoint.startswith("bid_details_"):
                rows = body.get("table2") if isinstance(body, dict) else body
                auxiliary[key]["bid_details"] = table_payload(rows, observed_at, url)
            elif endpoint.startswith("consolidated_bid_details_new_"):
                rows = body.get("table1") if isinstance(body, dict) else body
                auxiliary[key]["consolidated_bid_details_new"] = table_payload(rows, observed_at, url)
            elif endpoint.startswith("consolidated_bid_details_"):
                rows = body.get("table1") if isinstance(body, dict) else body
                auxiliary[key]["consolidated_bid_details"] = table_payload(rows, observed_at, url)
            elif endpoint.startswith("demand_schedule_"):
                rows = body.get("table1") if isinstance(body, dict) else body
                auxiliary[key]["demand_schedule"] = table_payload(rows, observed_at, url)
            elif endpoint.startswith("demand_graph_bse_"):
                auxiliary[key].setdefault("demand_graphs", {})["bse"] = {"url": url, "observed_at": observed_at}
            elif endpoint.startswith("demand_graph_consolidated_"):
                auxiliary[key].setdefault("demand_graphs", {})["consolidated"] = {"url": url, "observed_at": observed_at}
    return auxiliary


def exchange_details_for(record: dict[str, Any], auxiliary: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    details: dict[str, Any] = {}
    looked_up = set()
    for source in record.get("sources", []):
        source_name = source.get("source")
        source_record_id = source.get("source_record_id")
        if not source_name or not source_record_id:
            continue
        for source_key in possible_auxiliary_keys(record, source_name, source_record_id):
            key = (source_name, source_key)
            if key in looked_up:
                continue
            looked_up.add(key)
            item = auxiliary.get(key)
            if not item:
                continue
            details.setdefault(source_name, {}).update(item)
    return details


def possible_auxiliary_keys(record: dict[str, Any], source_name: str, source_record_id: str) -> list[str]:
    keys = [str(source_record_id).upper() if source_name == "nse" else str(source_record_id).lower()]
    symbol = record.get("symbol")
    if source_name == "nse" and symbol:
        keys.append(str(symbol).upper())
    company_name = record.get("company_name")
    if company_name:
        keys.append(f"company:{slugify(company_name)}")
    return list(dict.fromkeys(keys))


def nse_pan_auxiliary_keys(snapshots: list[dict[str, Any]]) -> dict[str, list[tuple[str, str]]]:
    pan_keys: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for snapshot in snapshots:
        meta = snapshot.get("meta", {})
        if meta.get("source") != "nse" or meta.get("endpoint") not in {"offer_documents_equity", "offer_documents_sme", "public_issue_advertisements"}:
            continue
        body = snapshot.get("body")
        if not isinstance(body, list):
            continue
        for row in body:
            if not isinstance(row, dict):
                continue
            pan = clean_key(row.get("pan_no") or row.get("panNo"))
            if not pan:
                continue
            keys = nse_public_ad_keys(row) if meta.get("endpoint") == "public_issue_advertisements" else nse_row_keys(row)
            for key in keys:
                if key not in pan_keys[pan.upper()]:
                    pan_keys[pan.upper()].append(key)
    return pan_keys


def nse_row_keys(row: dict[str, Any]) -> list[tuple[str, str]]:
    keys = []
    symbol = clean_key(row.get("symbol"))
    company = clean_key(row.get("company"))
    pan = clean_key(row.get("pan_no"))
    if symbol:
        keys.append(("nse", symbol.upper()))
    if company:
        keys.append(("nse", f"company:{slugify(company)}"))
    if pan:
        keys.append(("nse", pan.upper()))
    return list(dict.fromkeys(keys))


def nse_public_ad_keys(row: dict[str, Any]) -> list[tuple[str, str]]:
    keys = []
    company = clean_key(row.get("issuerName"))
    pan = clean_key(row.get("panNo"))
    if company:
        keys.append(("nse", f"company:{slugify(company)}"))
    if pan:
        keys.append(("nse", pan.upper()))
    return list(dict.fromkeys(keys))


def nse_market_action_keys(row: dict[str, Any]) -> list[tuple[str, str]]:
    keys = []
    symbol = clean_key(row.get("symbol") or row.get("symbolRG"))
    company = clean_key(row.get("company") or row.get("companyName"))
    if symbol:
        keys.append(("nse", symbol.upper()))
    if company:
        keys.append(("nse", f"company:{slugify(company)}"))
    return list(dict.fromkeys(keys))


def site_rows_from_body(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if isinstance(body, dict):
        rows = body.get("data")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def public_issue_detail_name(endpoint: str) -> str:
    if endpoint.startswith("rights_"):
        return "rights_details"
    if endpoint.startswith("ipp_"):
        return "ipp_details"
    if endpoint.startswith("invits_"):
        return "invit_details"
    if endpoint.startswith("reits_"):
        return "reit_details"
    if endpoint.startswith("zczp_"):
        return "zczp_details"
    if endpoint.startswith("lwf_"):
        return "lwf_details"
    return "public_issue_details"


def clean_key(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "--", "NA", "N/A", "null", "None"}:
        return None
    return text


def snapshot_payload(body: Any, observed_at: str | None, url: str | None) -> dict[str, Any]:
    return {"observed_at": observed_at, "url": url, "data": body}


def table_payload(rows: Any, observed_at: str | None, url: str | None, update_time: str | None = None) -> dict[str, Any]:
    clean_rows = rows if isinstance(rows, list) else []
    return {
        "observed_at": observed_at,
        "updated_at_source": update_time,
        "url": url,
        "row_count": len(clean_rows),
        "rows": clean_rows,
    }


def nse_offer_documents(row: dict[str, Any]) -> list[dict[str, str]]:
    documents = []
    for url_key, date_key, label in (
        ("drhpAttach", "drhpDate", "DRHP"),
        ("rhpAttach", "rhpDate", "RHP"),
        ("fpAttach", "fpDate", "Final prospectus"),
        ("advAttach", "advDate", "Public issue advertisement"),
        ("drhpAvLink", "drhpSubDate", "Audiovisual DRHP"),
        ("iapAvLink", "iapSubDate", "Audiovisual abridged prospectus"),
        ("icAvLink", "icSubDate", "Audiovisual investor charter"),
        ("ipo_inprincipal_xbrl_link", None, "In-principle approval XBRL"),
        ("ipo_inlisting_xbrl_link", None, "In-listing XBRL"),
        ("ipo_abridged_prospectus_xbrl_link", None, "Abridged prospectus XBRL"),
    ):
        url = clean_key(row.get(url_key))
        if not url:
            continue
        document = {"type": label, "url": url}
        if date_key:
            doc_date = clean_key(row.get(date_key))
            if doc_date:
                document["source_date"] = doc_date
        documents.append(document)
    return merged_documents(documents, [])


def nse_public_issue_ad_documents(row: dict[str, Any]) -> list[dict[str, str]]:
    documents = []
    for key, value in row.items():
        if not key.startswith("record") or not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            url = clean_key(item.get("attFilename"))
            if not url:
                continue
            documents.append(
                {
                    "type": clean_key(item.get("advertisementType")) or "Public issue advertisement",
                    "url": url,
                    "source_date": clean_key(item.get("submitDate")) or "",
                }
            )
    return merged_documents(documents, [])


def append_table_rows(target: dict[str, Any], rows: list[Any]) -> None:
    seen = {repr(row) for row in target.get("rows", [])}
    for row in rows:
        key = repr(row)
        if key in seen:
            continue
        target.setdefault("rows", []).append(row)
        seen.add(key)


def append_documents(target: list[dict[str, str]], documents: list[dict[str, str]]) -> None:
    existing = {(document.get("type"), document.get("url")) for document in target}
    for document in documents:
        key = (document.get("type"), document.get("url"))
        if key in existing:
            continue
        target.append(document)
        existing.add(key)


def exchange_documents(exchange_details: dict[str, Any]) -> list[dict[str, str]]:
    documents = []
    for details in exchange_details.values():
        if not isinstance(details, dict):
            continue
        for key in ("offer_documents", "public_issue_advertisements"):
            payload = details.get(key)
            if isinstance(payload, dict):
                documents.extend(payload.get("documents", []))
    return documents


def merged_documents(primary: list[dict[str, str]], secondary: list[dict[str, str]]) -> list[dict[str, str]]:
    merged = []
    append_documents(merged, primary)
    append_documents(merged, secondary)
    return merged
