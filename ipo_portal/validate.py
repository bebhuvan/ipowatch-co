from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

from .normalize import site_merge_key, slugify


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def expected_status(as_of: date, start: date | None, end: date | None) -> str | None:
    if not start or not end:
        return None
    if as_of < start:
        return "upcoming"
    if start <= as_of <= end:
        return "active"
    return "past"


def validate_records(records: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    by_id: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_id[record.get("id")].append(record)
    for record_id, group in by_id.items():
        if not record_id or len(group) <= 1:
            continue
        signatures = {
            (
                record.get("company_name"),
                record.get("issue_start_date"),
                record.get("issue_end_date"),
                record.get("source_record_id"),
            )
            for record in group
        }
        if len(signatures) > 1:
            errors.append({"code": "duplicate_id_conflict", "id": record_id, "count": len(group)})
        else:
            warnings.append({"code": "duplicate_record", "id": record_id, "count": len(group)})

    source_keys: dict[tuple[Any, Any, Any], dict[tuple[Any, Any, Any], list[Any]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        key = (record.get("source"), record.get("source_endpoint"), record.get("source_record_id"))
        signature = (record.get("company_name"), record.get("issue_start_date"), record.get("issue_end_date"))
        if all(key):
            source_keys[key][signature].append(record.get("id"))

    for key, signatures_by_id in source_keys.items():
        signatures = list(signatures_by_id)
        if len(signatures) > 1:
            warnings.append(
                {
                    "code": "source_identifier_reused",
                    "source": key[0],
                    "source_endpoint": key[1],
                    "source_record_id": key[2],
                    "ids": sorted({item for ids in signatures_by_id.values() for item in ids if item}),
                    "signatures": sorted([tuple("" if part is None else part for part in signature) for signature in signatures]),
                }
            )

    for record in records:
        record_ref = {
            "id": record.get("id"),
            "source": record.get("source"),
            "company_name": record.get("company_name"),
        }
        company = record.get("company_name")
        start = parse_iso_date(record.get("issue_start_date"))
        end = parse_iso_date(record.get("issue_end_date"))
        listing = parse_iso_date(record.get("listing_date"))
        observed_at = parse_observed_at(record.get("observed_at"))

        if not company:
            errors.append({"code": "missing_company_name", **record_ref})

        if observed_at and observed_at.date() > as_of:
            errors.append(
                {
                    "code": "observed_after_as_of",
                    "observed_at": observed_at.isoformat(),
                    "as_of": as_of.isoformat(),
                    **record_ref,
                }
            )

        if start and end and end < start:
            errors.append(
                {
                    "code": "issue_end_before_start",
                    "issue_start_date": record.get("issue_start_date"),
                    "issue_end_date": record.get("issue_end_date"),
                    **record_ref,
                }
            )

        if listing and end and listing < end:
            errors.append(
                {
                    "code": "listing_before_issue_end",
                    "listing_date": record.get("listing_date"),
                    "issue_end_date": record.get("issue_end_date"),
                    **record_ref,
                }
            )

        inferred = expected_status(as_of, start, end)
        status = (record.get("status") or "").lower()
        if inferred and status in {"upcoming", "active", "past"} and status != inferred:
            warnings.append(
                {
                    "code": "status_date_mismatch",
                    "source_status": record.get("status"),
                    "date_status": inferred,
                    "as_of": as_of.isoformat(),
                    **record_ref,
                }
            )

        if start and start > as_of and record.get("source_endpoint", "").endswith("past_issues"):
            warnings.append(
                {
                    "code": "future_record_in_past_feed",
                    "issue_start_date": record.get("issue_start_date"),
                    "as_of": as_of.isoformat(),
                    **record_ref,
                }
            )

        if listing and end and as_of < end:
            warnings.append(
                {
                    "code": "potential_listing_date_leakage",
                    "listing_date": record.get("listing_date"),
                    "issue_end_date": record.get("issue_end_date"),
                    "as_of": as_of.isoformat(),
                    **record_ref,
                }
            )

        raw_text = str(record.get("raw") or "")
        if any(token in raw_text for token in ("Select Date", "68.96,640")):
            warnings.append({"code": "suspicious_raw_value", **record_ref})

        if record.get("listing_date") and listing and listing > as_of:
            warnings.append(
                {
                    "code": "future_listing_date_visible",
                    "listing_date": record.get("listing_date"),
                    "as_of": as_of.isoformat(),
                    **record_ref,
                }
            )

        if record.get("issue_price") is not None and end and as_of < end:
            warnings.append(
                {
                    "code": "issue_price_visible_before_close",
                    "issue_end_date": record.get("issue_end_date"),
                    "as_of": as_of.isoformat(),
                    **record_ref,
                }
            )

    warnings.extend(cross_source_warnings(records))
    warnings.extend(site_key_aliases(records))

    return {
        "as_of": as_of.isoformat(),
        "record_count": len(records),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }


def parse_observed_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def cross_source_warnings(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    by_company: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        name = record.get("company_name")
        if not name:
            continue
        key = slugify(name)
        if key:
            by_company[key].append(record)

    for key, group in by_company.items():
        sources = {record.get("source") for record in group}
        if not {"nse", "bse"}.issubset(sources):
            continue

        dates = {
            (
                record.get("issue_start_date"),
                record.get("issue_end_date"),
            )
            for record in group
            if record.get("issue_start_date") or record.get("issue_end_date")
        }
        if len(dates) > 1:
            warnings.append(
                {
                    "code": "cross_source_date_mismatch",
                    "company_key": key,
                    "dates": sorted(list(dates)),
                    "sources": sorted(sources),
                }
            )

    return warnings


def site_key_aliases(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = site_merge_key(record)
        groups[key].append(record)

    for key, group in groups.items():
        company_names = {record.get("company_name") for record in group if record.get("company_name")}
        source_record_ids = {f"{record.get('source')}:{record.get('source_record_id')}" for record in group}
        if len(group) > 1 and len(company_names) > 1:
            warnings.append(
                {
                    "code": "site_merge_alias",
                    "site_key": key,
                    "company_names": sorted(company_names),
                    "source_record_ids": sorted(source_record_ids),
                }
            )
    return warnings
