"""Build the self-contained IPO Watch V3 site dataset.

The exporter is raw-first: when ``data/raw`` snapshots exist it runs the
existing source parsers/normalizer into a temporary canonical tree and then
materializes the V3 public contract. ``data/site_v2`` is retained only as a
comparison input and as a fallback for unit tests without raw fixtures.
"""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..storage import load_latest_snapshots


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_RAW_ROOT = PROJECT_ROOT / "data" / "raw"
DEFAULT_SOURCE_ROOT = PROJECT_ROOT / "data" / "site_v2"
DEFAULT_OUT_ROOT = PROJECT_ROOT / "data" / "ipo_watch_v3"
DEFAULT_SCHEMA_ROOT = PROJECT_ROOT / "docs" / "schema" / "v3"
V2_SCHEMA_ROOT = PROJECT_ROOT / "docs" / "schema" / "v2"
V3_SCHEMA_VERSION = "3.0.0"
TODAY = date(2026, 5, 24)


@dataclass(frozen=True)
class ExportStats:
    out_root: Path
    source_dataset_version: str | None
    dataset_version: str
    issues: int
    companies: int
    trajectories: int
    prospectuses: int
    json_files: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "out_root": str(self.out_root),
            "source_dataset_version": self.source_dataset_version,
            "dataset_version": self.dataset_version,
            "issues": self.issues,
            "companies": self.companies,
            "trajectories": self.trajectories,
            "prospectuses": self.prospectuses,
            "json_files": self.json_files,
        }


@dataclass(frozen=True)
class SubscriptionRefreshStats:
    out_root: Path
    source_root: Path
    active_slugs: list[str]
    updated_files: int
    missing_source_slugs: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "out_root": str(self.out_root),
            "source_root": str(self.source_root),
            "active_slugs": self.active_slugs,
            "updated_files": self.updated_files,
            "missing_source_slugs": self.missing_source_slugs,
        }


def export_v3(
    *,
    raw_root: Path = DEFAULT_RAW_ROOT,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    out_root: Path = DEFAULT_OUT_ROOT,
    schema_root: Path = DEFAULT_SCHEMA_ROOT,
) -> ExportStats:
    raw_root = raw_root.resolve()
    source_root = source_root.resolve()
    out_root = out_root.resolve()
    schema_root = schema_root.resolve()

    tmp_root = out_root.with_name(f".{out_root.name}.tmp")
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    tmp_root.mkdir(parents=True)
    existing_facts = _load_existing_prospectus_facts(out_root)

    use_raw_input = raw_root != DEFAULT_RAW_ROOT.resolve() or source_root == DEFAULT_SOURCE_ROOT.resolve()
    raw_snapshots = load_latest_snapshots(raw_root.parent) if use_raw_input else []
    derived_root = tmp_root / "_canonical_from_raw"
    raw_first = bool(raw_snapshots)
    freshness = _source_freshness(raw_snapshots) if raw_first else _empty_freshness()
    if raw_first:
        from ..normalize_v2.pipeline import run_normalize

        run_normalize(raw_root=raw_root, out_root=derived_root, schema_root=V2_SCHEMA_ROOT)
        canonical_root = derived_root
    elif source_root.exists():
        canonical_root = source_root
    else:
        raise FileNotFoundError(f"no raw snapshots at {raw_root} and no source tree at {source_root}")

    source_manifest = _read_json(canonical_root / "manifest.json") if (canonical_root / "manifest.json").exists() else {}
    source_version = source_manifest.get("dataset_version")
    generated_at = _latest_snapshot_at(raw_snapshots) or source_manifest.get("generated_at") or source_manifest.get("build_completed_at") or "1970-01-01T00:00:00+00:00"
    dataset_version = _dataset_version(generated_at, source_version)
    meta = {
        "schema_version": V3_SCHEMA_VERSION,
        "dataset_version": dataset_version,
        "source_dataset_version": source_version,
        "generated_at": generated_at,
        "generated_by": "ipo_portal.site_v3.export/3.0.0",
        "public_contract": "ipo-watch.site_v3",
    }

    records = [_read_json(p) for p in sorted((canonical_root / "issues" / "by-slug").glob("*.json"))]
    validation = _validate_records(records)
    public_records = [r for r in records if validation["states"].get(r.get("slug")) != "quarantined"]
    quarantined_records = [r for r in records if validation["states"].get(r.get("slug")) == "quarantined"]

    exchange_details = _exchange_details_by_symbol(raw_snapshots)

    public_issue_slugs = _public_issue_slug_map(public_records, validation)
    for record in public_records:
        _write_issue_bundle(tmp_root, record, meta, validation, existing_facts, exchange_details, public_issue_slugs)
    for record in quarantined_records:
        slug = record.get("slug")
        if slug:
            _write_json(tmp_root / "issues" / "quarantine" / f"{slug}.json", _issue_public_doc(record, meta, validation, public_slugs=public_issue_slugs))

    _copy_trajectories(canonical_root, tmp_root, meta)
    summaries = [_summary(r, validation, public_issue_slugs) for r in public_records]
    summaries.sort(key=lambda s: (s.get("listing_date") or s.get("close_date") or s.get("open_date") or "", s.get("company_name") or ""), reverse=True)
    companies = _write_indexes(tmp_root, summaries, meta)
    performance = _write_analytics(tmp_root, public_records, meta, public_issue_slugs)
    source_coverage = _source_coverage(raw_root, raw_snapshots)
    validation_report = _validation_report(validation, public_records, quarantined_records)
    v2_comparison = _v2_comparison(source_root, public_records, quarantined_records, validation)
    v2_comparison["v3_companies"] = len(companies)

    _write_meta(
        tmp_root=tmp_root,
        meta=meta,
        raw_first=raw_first,
        raw_root=raw_root,
        schema_root=schema_root,
        source_coverage=source_coverage,
        validation_report=validation_report,
        v2_comparison=v2_comparison,
        performance_count=len(performance),
        freshness=freshness,
    )

    shutil.rmtree(derived_root, ignore_errors=True)
    if out_root.exists():
        shutil.rmtree(out_root)
    tmp_root.replace(out_root)

    return ExportStats(
        out_root=out_root,
        source_dataset_version=source_version,
        dataset_version=dataset_version,
        issues=len(list((out_root / "issues" / "by-slug").glob("*.json"))),
        companies=len(list((out_root / "companies" / "by-slug").glob("*.json"))),
        trajectories=len(list((out_root / "trajectories").glob("*.json"))),
        prospectuses=len(list((out_root / "issues").glob("*/prospectus_facts.json"))),
        json_files=len(list(out_root.rglob("*.json"))),
    )


def update_v3_subscriptions(
    *,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    out_root: Path = DEFAULT_OUT_ROOT,
    slugs: list[str] | None = None,
) -> SubscriptionRefreshStats:
    """Refresh subscription-only V3 artifacts for active issues.

    This is intentionally narrower than ``export_v3``. It reads the latest
    normalized source records, updates only active issue subscription modules,
    the matching summary fields in public indexes, and subscription
    trajectories. Prospectus facts and other static issue modules are left
    untouched.
    """
    source_root = source_root.resolve()
    out_root = out_root.resolve()
    if not out_root.exists():
        raise FileNotFoundError(f"V3 root not found: {out_root}")
    if not source_root.exists():
        raise FileNotFoundError(f"source root not found: {source_root}")

    meta = _meta_from_existing_manifest(out_root)
    requested = set(slugs or _active_subscription_slugs(out_root))
    if not requested:
        return SubscriptionRefreshStats(out_root=out_root, source_root=source_root, active_slugs=[], updated_files=0, missing_source_slugs=[])

    updated = 0
    missing: list[str] = []
    summary_updates: dict[str, dict[str, Any]] = {}
    public_slugs = _existing_public_issue_slugs(out_root)
    for slug in sorted(requested):
        src_path = source_root / "issues" / "by-slug" / f"{slug}.json"
        if not src_path.exists():
            missing.append(slug)
            continue
        record = _read_json(src_path)
        subscription = record.get("subscription") or {}
        summary_updates[slug] = _summary(record, {"states": {slug: _existing_quality(out_root, slug)}}, public_slugs)
        updated += _write_json_if_changed(
            out_root / "issues" / slug / "subscription.json",
            _subscription_doc(record, meta),
        )

        issue_path = out_root / "issues" / "by-slug" / f"{slug}.json"
        if issue_path.exists():
            issue_doc = _read_json(issue_path)
            issue_doc["subscription"] = _subscription_payload(record)
            updated += _write_json_if_changed(issue_path, issue_doc)

        src_traj = source_root / "trajectories" / f"{slug}.json"
        if src_traj.exists():
            traj = _rewrite_urls(_read_json(src_traj))
            if isinstance(traj, dict):
                traj.update(meta)
                traj["$schema"] = _schema_url("trajectory")
            updated += _write_json_if_changed(out_root / "trajectories" / f"{slug}.json", traj)

    if summary_updates:
        updated += _update_subscription_summaries(out_root, summary_updates)

    return SubscriptionRefreshStats(
        out_root=out_root,
        source_root=source_root,
        active_slugs=sorted(requested),
        updated_files=updated,
        missing_source_slugs=missing,
    )


def _write_issue_bundle(
    root: Path,
    record: dict[str, Any],
    meta: dict[str, Any],
    validation: dict[str, Any],
    existing_facts: dict[str, dict[str, Any]] | None = None,
    exchange_details: dict[str, dict[str, Any]] | None = None,
    public_slugs: dict[str, str] | None = None,
) -> None:
    slug = record.get("slug")
    if not slug:
        return
    issue_dir = root / "issues" / slug
    public = _issue_public_doc(record, meta, validation, exchange_details, public_slugs)
    _write_json(root / "issues" / "by-slug" / f"{slug}.json", public)
    _write_json(issue_dir / "core.json", _core_doc(record, meta, validation, exchange_details))
    _write_json(issue_dir / "market.json", _market_doc(record, meta))
    _write_json(issue_dir / "subscription.json", _subscription_doc(record, meta))
    _write_json(issue_dir / "filings.json", _filings_doc(record, meta))
    existing = (existing_facts or {}).get(slug)
    _write_json(issue_dir / "prospectus_facts.json", existing or _prospectus_facts_doc(record, meta))
    _write_json(issue_dir / "provenance.json", _provenance_doc(record, meta, validation))


_HASH_SUFFIX_RE = re.compile(r"-[0-9a-f]{6}$")


def _clean_slug_stem(slug: str | None) -> str:
    stem = _HASH_SUFFIX_RE.sub("", str(slug or ""))
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return stem or "issue"


def _issue_date(record: dict[str, Any]) -> str | None:
    timeline = record.get("timeline") or {}
    for key in ("listing_date", "close_date", "open_date"):
        value = timeline.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _public_issue_slug_map(records: list[dict[str, Any]], validation: dict[str, Any]) -> dict[str, str]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        slug = record.get("slug")
        if not slug or validation["states"].get(slug) == "quarantined":
            continue
        identity = record.get("identity") or {}
        groups[(_issue_section(identity.get("issue_type")), _clean_slug_stem(slug))].append(record)

    out: dict[str, str] = {}
    used_by_section: dict[str, set[str]] = defaultdict(set)
    for (section, base), items in sorted(groups.items()):
        taken = used_by_section[section]
        for record in sorted(items, key=lambda r: (_issue_date(r) or "", str(r.get("slug") or ""))):
            slug = str(record.get("slug") or "")
            date_value = _issue_date(record)
            year = date_value[:4] if date_value and len(date_value) >= 4 else None
            short_id = slug[-6:] if re.search(r"[0-9a-f]{6}$", slug) else None
            candidates = [base] if len(items) == 1 else [
                f"{base}-{year}" if year else None,
                f"{base}-{date_value}" if date_value else None,
                f"{base}-{short_id}" if short_id else None,
                slug,
            ]
            chosen = next((c for c in candidates if c and c not in taken), slug)
            taken.add(chosen)
            out[slug] = chosen
    return out


def _public_issue_slug(slug: str | None, public_slugs: dict[str, str] | None = None) -> str:
    if slug and public_slugs and slug in public_slugs:
        return public_slugs[slug]
    return _clean_slug_stem(slug)


def _existing_public_issue_slugs(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted((root / "issues" / "by-slug").glob("*.json")):
        doc = _read_json(path)
        slug = doc.get("slug") or path.stem
        public_slug = doc.get("public_slug") or _clean_slug_stem(slug)
        out[str(slug)] = str(public_slug)
    return out


def _issue_url_path(slug: str | None, issue_type: str | None, public_slugs: dict[str, str] | None = None) -> str:
    return f"/{_issue_section(issue_type)}/{_public_issue_slug(slug, public_slugs)}/"


def _issue_section(issue_type: str | None) -> str:
    key = "".join(ch for ch in str(issue_type or "").lower() if ch.isalnum())
    if key in {"", "ipo", "initialpublicoffer", "initialpublicoffering", "fpo", "followonpublicoffer"}:
        return "ipos"
    if key in {"buyback", "buy", "tender"}:
        return "buybacks"
    if key in {"ofs", "offerforsale"}:
        return "ofs"
    if key in {"rights", "right", "rightsissue"}:
        return "rights"
    if key in {"ncd", "debt", "dpi"}:
        return "debt"
    if key in {"reit", "reits"}:
        return "reits"
    if key in {"invit", "invits"}:
        return "invits"
    if key in {"callmoney", "cmn"}:
        return "call-money"
    return "public-issues"


def _issue_public_doc(
    record: dict[str, Any],
    meta: dict[str, Any],
    validation: dict[str, Any],
    exchange_details: dict[str, dict[str, Any]] | None = None,
    public_slugs: dict[str, str] | None = None,
) -> dict[str, Any]:
    slug = record.get("slug") or (record.get("identity") or {}).get("slug")
    identity = record.get("identity") or {}
    public_slug = _public_issue_slug(slug, public_slugs)
    symbol = str(identity.get("symbol") or "").strip().upper()
    doc = {
        **meta,
        "$schema": _schema_url("issue-core"),
        "dataset": "ipo-watch.site_v3.issue",
        "slug": slug,
        "public_slug": public_slug,
        "url_path": _issue_url_path(slug, identity.get("issue_type"), public_slugs),
        "core_path": f"issues/{slug}/core.json",
        "market_path": f"issues/{slug}/market.json",
        "subscription_path": f"issues/{slug}/subscription.json",
        "filings_path": f"issues/{slug}/filings.json",
        "prospectus_facts_path": f"issues/{slug}/prospectus_facts.json",
        "provenance_path": f"issues/{slug}/provenance.json",
        "identity": identity,
        "timeline": record.get("timeline") or {},
        "pricing": record.get("pricing") or {},
        "subscription": _subscription_payload(record),
        "listing_performance": record.get("listing_performance") or {},
        "documents": record.get("documents") or {},
        "parties": record.get("parties") or {},
        "book_building": record.get("book_building") or {},
        "exchange_details": (exchange_details or {}).get(symbol, {}),
        "field_provenance": record.get("field_provenance") or {},
        "sources": record.get("sources") or [],
        "data_quality": _quality(slug, validation),
        "prospectus_available": True,
    }
    return _rewrite_urls(doc)


def _core_doc(
    record: dict[str, Any],
    meta: dict[str, Any],
    validation: dict[str, Any],
    exchange_details: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    slug = record.get("slug")
    symbol = str((record.get("identity") or {}).get("symbol") or "").strip().upper()
    return {
        **meta,
        "$schema": _schema_url("issue-core"),
        "slug": slug,
        "identity": record.get("identity") or {},
        "timeline": record.get("timeline") or {},
        "pricing": record.get("pricing") or {},
        "parties": record.get("parties") or {},
        "book_building": record.get("book_building") or {},
        "exchange_details": (exchange_details or {}).get(symbol, {}),
        "quality_state": validation["states"].get(slug, "review"),
        "findings": validation["findings_by_slug"].get(slug, {}),
    }


def _market_doc(record: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    pricing = record.get("pricing") or {}
    perf = record.get("listing_performance") or {}
    issue_price = pricing.get("issue_price_paise")
    listing_open = perf.get("listing_open_price_paise")
    listing_close = perf.get("listing_close_price_paise")
    current = perf.get("current_price_paise")
    return {
        **meta,
        "$schema": _schema_url("market"),
        "slug": record.get("slug"),
        "listing_date": (record.get("timeline") or {}).get("listing_date"),
        "issue_price_paise": issue_price,
        "listing_open_price_paise": listing_open,
        "listing_close_price_paise": listing_close,
        "listing_open_return_bps": _bps(issue_price, listing_open),
        "listing_close_return_bps": _bps(issue_price, listing_close),
        "current_price_paise": current,
        "current_return_from_issue_bps": _bps(issue_price, current),
        "cagr_bps": _cagr_bps(issue_price, current, (record.get("timeline") or {}).get("listing_date")),
        "max_drawdown_bps": perf.get("max_drawdown_bps"),
        "source": _market_source(record),
        "source_quality": _market_quality(record),
    }


def _subscription_doc(record: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    return {
        **meta,
        "$schema": _schema_url("subscription"),
        "slug": record.get("slug"),
        "subscription": _subscription_payload(record),
    }


def _subscription_payload(record: dict[str, Any]) -> dict[str, Any]:
    subscription = dict(record.get("subscription") or {})
    if not _has_subscription_categories(subscription):
        availability = _subscription_availability(record)
        if availability:
            subscription["data_availability"] = availability
    return subscription


def _has_subscription_categories(subscription: dict[str, Any]) -> bool:
    if subscription.get("consolidated", {}).get("categories"):
        return True
    by_exchange = subscription.get("by_exchange") or {}
    if isinstance(by_exchange, dict):
        return any(isinstance(book, dict) and book.get("categories") for book in by_exchange.values())
    return False


def _subscription_availability(record: dict[str, Any]) -> dict[str, Any] | None:
    identity = record.get("identity") or {}
    issue_type = identity.get("issue_type")
    sources = record.get("sources") or []
    source_refs = [
        {
            "source": source.get("source"),
            "endpoint": source.get("endpoint"),
            "snapshot_at": source.get("snapshot_at"),
        }
        for source in sources
        if isinstance(source, dict)
    ]
    if issue_type == "Rights" and any((s.get("endpoint") or "").startswith("rights_") for s in source_refs):
        return {
            "state": "exchange_not_provided",
            "reason": "NSE rights issue feed currently provides identity and offer-window fields only for this row; subscription/demand fields are null.",
            "expected_product_feed": "nse.rights_active_or_forthcoming",
            "source_refs": source_refs,
        }
    if issue_type == "Buyback" and any(s.get("source") == "bse" for s in source_refs):
        return {
            "state": "not_applicable_to_ipo_subscription_book",
            "reason": "BSE classifies this row as OTB/buyback. IPO/FPO bid-book category endpoints are not applicable; buyback tender acceptance/demand needs a separate product-specific parser.",
            "expected_product_feed": "bse.buyback_tender_detail",
            "source_refs": source_refs,
        }
    return None


def _filings_doc(record: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    labels = {
        "drhp_url": "DRHP",
        "rhp_url": "RHP",
        "prospectus_url": "Prospectus",
        "basis_allotment_url": "Basis of allotment",
    }
    docs = []
    for field, url in sorted((record.get("documents") or {}).items()):
        if isinstance(url, str) and url:
            docs.append({"field": field, "type": labels.get(field, field), "url": url, "verified": True})
    return {**meta, "$schema": _schema_url("filings"), "slug": record.get("slug"), "documents": docs}


_NSE_SYMBOL_ENDPOINT_RE = re.compile(
    r"^(issue_detail|bid_details|consolidated_bid_details|demand_data_nse|demand_data_all)_([a-z0-9]+)(?:_[a-z0-9]+)?$",
    re.IGNORECASE,
)


def _exchange_details_by_symbol(raw_snapshots: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return compact per-symbol exchange detail payloads for page rendering.

    V3 keeps normalized scalar fields as the canonical contract, but the NSE
    current IPO page exposes a useful title/value `issueInfo` table. Keeping a
    compact copy here lets Astro render all NSE-published issue-detail rows
    without reading `data/raw` at build/runtime.
    """
    out: dict[str, dict[str, Any]] = {}
    for snapshot in raw_snapshots:
        meta = snapshot.get("meta") or {}
        if meta.get("source") != "nse":
            continue
        endpoint = str(meta.get("endpoint") or "")
        match = _NSE_SYMBOL_ENDPOINT_RE.match(endpoint)
        if not match:
            continue
        kind, symbol_key = match.groups()
        symbol = symbol_key.upper()
        body = snapshot.get("body")
        if not isinstance(body, (dict, list)):
            continue
        bucket = out.setdefault(symbol, {}).setdefault("nse", {})
        payload = {"data": body, "snapshot_at": meta.get("fetched_at"), "url": meta.get("url")}
        if kind.lower() == "issue_detail":
            bucket["issue_detail"] = payload
        elif kind.lower() == "bid_details":
            bucket["bid_details"] = payload
        elif kind.lower() == "consolidated_bid_details":
            bucket["consolidated_bid_details"] = payload
        elif kind.lower() == "demand_data_nse":
            bucket.setdefault("demand_data", {})["nse"] = payload
        elif kind.lower() == "demand_data_all":
            bucket.setdefault("demand_data", {})["all"] = payload
    return out


def _prospectus_facts_doc(record: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    docs = record.get("documents") or {}
    has_doc = bool(docs.get("rhp_url") or docs.get("drhp_url") or docs.get("prospectus_url"))
    return {
        **meta,
        "$schema": _schema_url("prospectus-facts"),
        "slug": record.get("slug"),
        "extraction_status": "not_extracted" if has_doc else "no_prospectus_document",
        "facts": {},
        "redactions": [],
        "citation_policy": "Scalar facts are published only with value, raw_excerpt, source_page, source_section, confidence, and verified pdftotext citation.",
        "deepseek": {"used": False, "cache_key": None, "cost_usd": "0.0000", "tokens_in": 0, "tokens_out": 0},
    }


def _provenance_doc(record: dict[str, Any], meta: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    slug = record.get("slug")
    return {
        **meta,
        "$schema": _schema_url("provenance"),
        "slug": slug,
        "sources": record.get("sources") or [],
        "field_provenance": record.get("field_provenance") or {},
        "validation_state": validation["states"].get(slug, "review"),
        "findings": validation["findings_by_slug"].get(slug, {}),
    }


def _summary(record: dict[str, Any], validation: dict[str, Any], public_slugs: dict[str, str] | None = None) -> dict[str, Any]:
    identity = record.get("identity") or {}
    pricing = record.get("pricing") or {}
    timeline = record.get("timeline") or {}
    perf = record.get("listing_performance") or {}
    subs = record.get("subscription") or {}
    slug = record.get("slug") or identity.get("slug")
    public_slug = _public_issue_slug(slug, public_slugs)
    return {
        "slug": slug,
        "public_slug": public_slug,
        "company_name": identity.get("company_name"),
        "symbol": identity.get("symbol"),
        "isin": identity.get("isin"),
        "issue_type": identity.get("issue_type"),
        "board_type": identity.get("board_type"),
        "status": identity.get("status"),
        "open_date": timeline.get("open_date"),
        "close_date": timeline.get("close_date"),
        "listing_date": timeline.get("listing_date"),
        "price_band_lower_paise": pricing.get("price_band_lower_paise"),
        "price_band_upper_paise": pricing.get("price_band_upper_paise"),
        "issue_price_paise": pricing.get("issue_price_paise"),
        "issue_size_paise": pricing.get("issue_size_paise"),
        "overall_times_x": subs.get("overall_times_x"),
        "listing_gain_bps": perf.get("listing_gain_bps"),
        "current_gain_bps": perf.get("current_gain_bps"),
        "url_path": _issue_url_path(slug, identity.get("issue_type"), public_slugs),
        "data_quality_state": validation["states"].get(slug, "review"),
        "source_count": len(record.get("sources") or []),
    }


def _write_indexes(root: Path, summaries: list[dict[str, Any]], meta: dict[str, Any]) -> list[dict[str, Any]]:
    _write_json(root / "issues" / "index.json", _index_doc(meta, "issues/index.schema", summaries))
    buckets: dict[str, dict[str, list[dict[str, Any]]]] = {"by-year": defaultdict(list), "by-status": defaultdict(list), "by-kind": defaultdict(list)}
    for row in summaries:
        year = next((str(row[k])[:4] for k in ("listing_date", "close_date", "open_date") if row.get(k)), "undated")
        buckets["by-year"][year].append(row)
        buckets["by-status"][(row.get("status") or "unknown").lower()].append(row)
        buckets["by-kind"][(row.get("issue_type") or "unknown").lower().replace(" ", "_")].append(row)
    for year, items in sorted(buckets["by-year"].items()):
        _write_json(root / "issues" / "by-year" / f"{year}.json", _index_doc(meta, "issues/by-year.schema", items, {"year": year}))
    _write_json(root / "issues" / "by-year" / "index.json", _index_doc(meta, "issues/by-year-index.schema", [{"year": y, "count": len(v)} for y, v in sorted(buckets["by-year"].items(), reverse=True)]))
    for status, items in sorted(buckets["by-status"].items()):
        _write_json(root / "issues" / "by-status" / f"{status}.json", _index_doc(meta, "issues/by-status.schema", items, {"status": status}))
    for kind, items in sorted(buckets["by-kind"].items()):
        _write_json(root / "issues" / "by-kind" / f"{kind}.json", _index_doc(meta, "issues/by-kind.schema", items, {"kind": kind}))
    return _write_companies(root, summaries, meta)


def _write_companies(root: Path, summaries: list[dict[str, Any]], meta: dict[str, Any]) -> list[dict[str, Any]]:
    from ..normalize_v2.identity import normalize_name, slugify

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in summaries:
        key = normalize_name(row.get("company_name") or "")
        if key:
            grouped[key].append(row)
    internal_slugs = {key: slugify(key, f"company:{key}") for key in grouped}
    public_slugs = _public_company_slug_map(internal_slugs)
    index = []
    for key, items in sorted(grouped.items()):
        items = sorted(items, key=lambda r: (r.get("listing_date") or r.get("close_date") or r.get("open_date") or ""), reverse=True)
        slug = internal_slugs[key]
        public_slug = public_slugs[slug]
        record = {
            **meta,
            "$schema": _schema_url("company"),
            "slug": slug,
            "public_slug": public_slug,
            "company_name": items[0].get("company_name"),
            "normalized_name": key,
            "url_path": f"/companies/{public_slug}/",
            "issue_count": len(items),
            "issues": items,
        }
        _write_json(root / "companies" / "by-slug" / f"{slug}.json", record)
        index.append({"slug": slug, "public_slug": public_slug, "company_name": record["company_name"], "issue_count": len(items), "url_path": record["url_path"]})
    index.sort(key=lambda r: r.get("company_name") or "")
    _write_json(root / "companies" / "index.json", _index_doc(meta, "companies/index.schema", index))
    return index


def _public_company_slug_map(internal_slugs: dict[str, str]) -> dict[str, str]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for slug in internal_slugs.values():
        grouped[_clean_slug_stem(slug)].append(slug)
    out: dict[str, str] = {}
    taken: set[str] = set()
    for base, slugs in sorted(grouped.items()):
        for slug in sorted(slugs):
            short_id = slug[-6:] if re.search(r"[0-9a-f]{6}$", slug) else None
            candidates = [base] if len(slugs) == 1 else [base, f"{base}-{short_id}" if short_id else None, slug]
            chosen = next((c for c in candidates if c and c not in taken), slug)
            taken.add(chosen)
            out[slug] = chosen
    return out


def _write_analytics(root: Path, records: list[dict[str, Any]], meta: dict[str, Any], public_slugs: dict[str, str] | None = None) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        identity = record.get("identity") or {}
        if identity.get("issue_type") != "IPO":
            continue
        market = _market_doc(record, meta)
        if not any(market.get(k) is not None for k in ("listing_open_price_paise", "listing_close_price_paise", "current_price_paise")):
            continue
        rows.append({
            "slug": record.get("slug"),
            "public_slug": _public_issue_slug(record.get("slug"), public_slugs),
            "url_path": _issue_url_path(record.get("slug"), identity.get("issue_type"), public_slugs),
            "company_name": identity.get("company_name"),
            "symbol": identity.get("symbol"),
            "listing_date": market.get("listing_date"),
            "issue_price_paise": market.get("issue_price_paise"),
            "listing_open_price_paise": market.get("listing_open_price_paise"),
            "listing_close_price_paise": market.get("listing_close_price_paise"),
            "current_price_paise": market.get("current_price_paise"),
            "listing_close_return_bps": market.get("listing_close_return_bps"),
            "current_return_from_issue_bps": market.get("current_return_from_issue_bps"),
            "source": market.get("source"),
            "source_quality": market.get("source_quality"),
        })
    rows.sort(key=lambda r: r.get("listing_date") or "", reverse=True)
    _write_json(root / "analytics" / "performance.json", {**meta, "count": len(rows), "rows": rows})
    year_counts = Counter((r.get("listing_date") or "undated")[:4] for r in rows)
    _write_json(root / "analytics" / "cohorts.json", {**meta, "years": [{"year": y, "count": c} for y, c in sorted(year_counts.items(), reverse=True)]})
    source_counts = Counter((r.get("source") or {}).get("name") or "missing" for r in rows)
    _write_json(root / "analytics" / "source_quality.json", {**meta, "market_source_mix": dict(sorted(source_counts.items()))})
    return rows


def _empty_freshness() -> dict[str, Any]:
    return {"degraded": False, "stale_sources": [], "sources": {}}


def _source_freshness(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    latest: dict[str, datetime] = {}
    endpoint_counts: Counter[str] = Counter()
    for snapshot in snapshots:
        meta = snapshot.get("meta") or {}
        source = meta.get("source")
        fetched_at = _parse_dt(meta.get("fetched_at"))
        if not source or fetched_at is None:
            continue
        endpoint_counts[str(source)] += 1
        key = str(source)
        if key not in latest or fetched_at > latest[key]:
            latest[key] = fetched_at

    now = datetime.now(timezone.utc).replace(microsecond=0)
    tolerances = {
        "sebi": 24,
        "nse": 2,
        "bse": 2,
        "yahoo": 24,
        "kite": 24,
    }
    required = {"sebi", "nse", "bse"}
    sources: dict[str, dict[str, Any]] = {}
    stale: list[dict[str, Any]] = []

    for source in sorted(set(tolerances) | set(latest)):
        fetched_at = latest.get(source)
        tolerance = tolerances.get(source, 24)
        if fetched_at is None:
            state = "missing"
            age_hours = None
        else:
            age_hours = round((now - fetched_at).total_seconds() / 3600, 3)
            state = "fresh" if age_hours <= tolerance else "stale"
        entry = {
            "source": source,
            "state": state,
            "latest_fetched_at": fetched_at.isoformat() if fetched_at else None,
            "age_hours": age_hours,
            "staleness_tolerance_hours": tolerance,
            "endpoint_count": endpoint_counts.get(source, 0),
        }
        sources[source] = entry
        if source in required and state != "fresh":
            stale.append(entry)

    market_fresh = any(sources.get(source, {}).get("state") == "fresh" for source in ("kite", "yahoo"))
    if not market_fresh:
        stale.append(
            {
                "source": "market_data",
                "state": "missing_or_stale",
                "latest_fetched_at": None,
                "age_hours": None,
                "staleness_tolerance_hours": 24,
                "endpoint_count": 0,
                "reason": "Neither Kite nor Yahoo has a fresh performance snapshot.",
            }
        )

    return {"degraded": bool(stale), "stale_sources": stale, "sources": sources}


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _write_meta(
    *,
    tmp_root: Path,
    meta: dict[str, Any],
    raw_first: bool,
    raw_root: Path,
    schema_root: Path,
    source_coverage: dict[str, Any],
    validation_report: dict[str, Any],
    v2_comparison: dict[str, Any],
    performance_count: int,
    freshness: dict[str, Any],
) -> None:
    issue_count = len(list((tmp_root / "issues" / "by-slug").glob("*.json")))
    company_count = len(list((tmp_root / "companies" / "by-slug").glob("*.json")))
    trajectory_count = len(list((tmp_root / "trajectories").glob("*.json")))
    manifest = {
        **meta,
        "$schema": _schema_url("manifest"),
        "dataset": "ipo-watch.site_v3.manifest",
        "raw_first": raw_first,
        "raw_root": str(raw_root),
        "issues_total": issue_count,
        "issues_published": issue_count,
        "issues_review_tier": validation_report["review_count"],
        "issues_quarantined": validation_report["quarantined_count"],
        "companies_total": company_count,
        "trajectories_total": trajectory_count,
        "performance_rows": performance_count,
        "degraded": freshness["degraded"],
        "stale_sources": freshness["stale_sources"],
        "source_freshness": freshness["sources"],
        "site_contract": {
            "schema_version": V3_SCHEMA_VERSION,
            "self_contained": True,
            "root": "data/ipo_watch_v3",
            "units": {"money": "integer paise", "percent": "integer basis points", "subscription_multiple": "decimal string", "dates": "ISO 8601", "timezone": "Asia/Kolkata", "currency": "INR"},
        },
    }
    _write_json(tmp_root / "manifest.json", manifest)
    _write_json(tmp_root / "_meta" / "contract.json", {**meta, "self_contained": True, "description": "IPO Watch V3 public static data contract.", "canonical_paths": {"issue": "issues/by-slug/<slug>.json", "core": "issues/<slug>/core.json", "market": "issues/<slug>/market.json", "subscription": "issues/<slug>/subscription.json", "filings": "issues/<slug>/filings.json", "prospectus_facts": "issues/<slug>/prospectus_facts.json", "provenance": "issues/<slug>/provenance.json"}})
    _write_json(tmp_root / "_meta" / "build_report.json", {**meta, "raw_first": raw_first, "issues_written": issue_count, "companies_written": company_count, "trajectories_written": trajectory_count, "source_endpoint_count": source_coverage["summary"]["total_endpoints"], "fetch_failed": source_coverage["summary"]["fetch_failed"], "parser_failed": source_coverage["summary"]["parser_failed"], "degraded": freshness["degraded"], "stale_sources": freshness["stale_sources"], "source_freshness": freshness["sources"], "deepseek": {"used": False, "cost_usd": "0.0000", "reason": "No verified prospectus extraction was run in this build; facts remain redacted."}})
    _write_json(tmp_root / "_meta" / "source_coverage.json", source_coverage)
    _write_json(tmp_root / "_meta" / "validation_report.json", validation_report)
    _write_json(tmp_root / "_meta" / "v2_comparison.json", v2_comparison)
    _copy_schemas(schema_root, tmp_root / "_meta" / "schemas")


def _validate_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    states = {}
    findings_by_slug = {}
    classes: dict[str, list[str]] = defaultdict(list)
    for record in records:
        slug = record.get("slug") or "unknown"
        blocking = []
        review = []
        idn = record.get("identity") or {}
        tl = record.get("timeline") or {}
        pricing = record.get("pricing") or {}
        perf = record.get("listing_performance") or {}
        docs = record.get("documents") or {}
        if not idn.get("company_name"):
            blocking.append(_finding("identity.no_company_name", "Missing company name"))
        if idn.get("isin") and not _valid_isin(str(idn["isin"])):
            blocking.append(_finding("identity.bad_isin", "Invalid ISIN"))
        if _date(tl.get("open_date")) and _date(tl.get("close_date")) and _date(tl.get("open_date")) > _date(tl.get("close_date")):
            blocking.append(_finding("date.open_after_close", "Open date is after close date"))
        if _date(tl.get("close_date")) and _date(tl.get("listing_date")) and _date(tl.get("close_date")) > _date(tl.get("listing_date")):
            blocking.append(_finding("date.close_after_listing", "Close date is after listing date"))
        for key in ("issue_price_paise", "price_band_lower_paise", "price_band_upper_paise"):
            value = pricing.get(key)
            if isinstance(value, int) and value <= 0:
                blocking.append(_finding(f"pricing.{key}.nonpositive", "Non-positive price"))
            if isinstance(value, int) and value < 100:
                blocking.append(_finding(f"pricing.{key}.under_1rupee", "Price below INR 1"))
        if isinstance(perf.get("listing_gain_bps"), int) and perf["listing_gain_bps"] < -10000:
            blocking.append(_finding("market.listing_gain_below_minus_100pct", "Listing loss below -100%"))
        if isinstance(perf.get("current_price_paise"), int) and perf["current_price_paise"] <= 0:
            blocking.append(_finding("market.current_price_nonpositive", "Current price is non-positive"))
        if _is_name_only(record) and record.get("sources"):
            blocking.append(_finding("garbage.name_only", "Name-only record"))
        elif _is_name_only(record):
            review.append(_finding("garbage.name_only", "Name-only record"))
        if idn.get("status") in {"Open", "Upcoming"} and _date(tl.get("close_date")) and _date(tl.get("close_date")) < TODAY:
            review.append(_finding("status.stale_active_state", "Open/upcoming issue has past close date"))
        if not docs and not any(tl.get(k) for k in ("open_date", "close_date", "listing_date")):
            review.append(_finding("thin.no_dates_or_documents", "No dates or documents"))
        if not pricing:
            review.append(_finding("thin.no_pricing", "No pricing"))
        state = "quarantined" if blocking else "review" if review else "clean"
        states[slug] = state
        findings_by_slug[slug] = {"blocking": blocking, "review": review, "info": []}
        for item in blocking + review:
            classes[item["class"]].append(slug)
    return {"states": states, "findings_by_slug": findings_by_slug, "classes": classes}


def _validation_report(validation: dict[str, Any], public_records: list[dict[str, Any]], quarantined_records: list[dict[str, Any]]) -> dict[str, Any]:
    states = validation["states"]
    classes = {k: {"count": len(v), "sample_slugs": sorted(set(v))[:10]} for k, v in sorted(validation["classes"].items())}
    return {
        "schema_version": V3_SCHEMA_VERSION,
        "clean_count": sum(1 for s in states.values() if s == "clean"),
        "review_count": sum(1 for s in states.values() if s == "review"),
        "quarantined_count": sum(1 for s in states.values() if s == "quarantined"),
        "removed_from_public_index_count": len(quarantined_records),
        "public_count": len(public_records),
        "quarantined_slugs": [r.get("slug") for r in quarantined_records[:100]],
        "finding_classes": classes,
        "garbage_policy": ["Name-only records are quarantined.", "Impossible prices and impossible date order are quarantined.", "Invalid ISINs are quarantined.", "Stale active statuses and thin records are review-tier.", "Unverified prospectus facts are redacted."],
    }


def _source_coverage(raw_root: Path, snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    from ..normalize_v2 import parsers as parser_pkg
    from ..normalize_v2.parsers import parser_for
    from ..sources import bse_endpoints, nse_endpoints

    parser_pkg.register_concrete_endpoints()
    expected = {(e.source, e.name, e.url) for e in [*nse_endpoints(), *bse_endpoints(TODAY)]}
    latest = {}
    for snap in snapshots:
        meta = snap.get("meta") or {}
        latest[(meta.get("source"), meta.get("endpoint"))] = snap
    rows = []
    seen = set()
    for source, endpoint, url in sorted(expected):
        snap = latest.get((source, endpoint))
        rows.append(_coverage_row(source, endpoint, url, snap, parser_for(source, endpoint)))
        seen.add((source, endpoint))
    for (source, endpoint), snap in sorted(latest.items()):
        if (source, endpoint) not in seen:
            rows.append(_coverage_row(str(source), str(endpoint), (snap.get("meta") or {}).get("url"), snap, parser_for(str(source), str(endpoint))))
    counts = Counter(row["classification"] for row in rows)
    return {
        "schema_version": V3_SCHEMA_VERSION,
        "raw_root": str(raw_root),
        "summary": {
            "total_endpoints": len(rows),
            "parsed_into_v3_canonical_records": counts["parsed into V3 canonical records"],
            "parsed_as_document_metadata_only": counts["parsed as document metadata only"],
            "intentionally_ignored_helper_or_dropdown_feed": counts["intentionally ignored helper/dropdown feed"],
            "unsupported_gap": counts["unsupported gap"],
            "fetch_failed": counts["fetch failed"],
            "parser_failed": counts["parser failed"],
            "unclassified": counts["unclassified"],
        },
        "endpoints": rows,
    }


def _coverage_row(source: str, endpoint: str, url: str | None, snap: dict[str, Any] | None, parser: Any) -> dict[str, Any]:
    status = ((snap or {}).get("meta") or {}).get("status_code")
    if snap is None or (isinstance(status, int) and status >= 400):
        klass = "fetch failed"
    elif endpoint.endswith("companylist") or endpoint in {"ipo_years", "ofs_date_list", "zczp_company_list", "public_issue_company_list", "ipo_past_security_type", "bond_issuance_years"}:
        klass = "intentionally ignored helper/dropdown feed"
    elif _out_of_scope_endpoint(endpoint):
        klass = "intentionally ignored helper/dropdown feed"
    elif parser is not None:
        klass = "parsed as document metadata only" if any(t in endpoint for t in ("offer_documents", "ipo_documents", "documents", "filings", "advertisements")) else "parsed into V3 canonical records"
    elif source in {"capitalmarket", "prime", "trendlyne", "moneycontrol"}:
        klass = "unsupported gap"
    else:
        klass = "parser failed"
    return {"source": source, "endpoint": endpoint, "url": url, "status_code": status, "snapshot_at": ((snap or {}).get("meta") or {}).get("fetched_at"), "classification": klass}


def _out_of_scope_endpoint(endpoint: str) -> bool:
    return endpoint in {"sgb_live_issues", "zczp_active", "zczp_company_list", "zczp_forthcoming", "zczp_past"} or endpoint.startswith(("lwf", "mfss", "ncbgsec", "noncompbid"))


def _v2_comparison(source_root: Path, public_records: list[dict[str, Any]], quarantined_records: list[dict[str, Any]], validation: dict[str, Any]) -> dict[str, Any]:
    manifest = _read_json(source_root / "manifest.json") if (source_root / "manifest.json").exists() else {}
    v2_slugs = {p.stem for p in (source_root / "issues" / "by-slug").glob("*.json")} if source_root.exists() else set()
    v3_slugs = {r.get("slug") for r in public_records}
    q_slugs = sorted(r.get("slug") for r in quarantined_records if r.get("slug"))
    return {
        "schema_version": V3_SCHEMA_VERSION,
        "v2_issue_count": manifest.get("issues_published") or len(v2_slugs),
        "v3_clean_public_count": sum(1 for r in public_records if validation["states"].get(r.get("slug")) == "clean"),
        "v3_public_count": len(public_records),
        "v2_companies": manifest.get("companies_total"),
        "v3_companies": None,
        "records_removed_from_public_v3": [{"slug": s, "reason": validation["findings_by_slug"].get(s, {})} for s in q_slugs[:500]],
        "records_added_freshly_discovered": sorted(v3_slugs - v2_slugs)[:500],
        "fields_improved": ["componentized issue contract", "endpoint-level source coverage", "stricter public-index quarantine", "field-level provenance module", "market analytics aggregate"],
        "fields_redacted": ["unverified prospectus scalar facts"],
        "provenance_improvements": ["source_coverage.json classifies every fetched/expected endpoint", "provenance.json is emitted per issue"],
        "market_data_improvements": ["returns are recomputed from paise prices", "Yahoo/Kite/exchange source quality is surfaced"],
        "prospectus_facts_added": 0,
        "suspected_v2_defects_not_carried_forward": q_slugs[:100],
    }


def _copy_trajectories(canonical_root: Path, out_root: Path, meta: dict[str, Any]) -> None:
    src = canonical_root / "trajectories"
    if not src.exists():
        return
    for path in sorted(src.glob("*.json")):
        doc = _rewrite_urls(_read_json(path))
        if isinstance(doc, dict):
            doc.update(meta)
            doc["$schema"] = _schema_url("trajectory")
        _write_json(out_root / "trajectories" / path.name, doc)


def _copy_schemas(schema_root: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    if not schema_root.exists():
        return
    for schema in sorted(schema_root.glob("*.schema.json")):
        _write_json(out / schema.name, _read_json(schema))


def _load_existing_prospectus_facts(out_root: Path) -> dict[str, dict[str, Any]]:
    facts: dict[str, dict[str, Any]] = {}
    if not out_root.exists():
        return facts
    for path in sorted((out_root / "issues").glob("*/prospectus_facts.json")):
        try:
            doc = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        status = doc.get("extraction_status")
        quality_state = (doc.get("quality") or {}).get("state")
        has_model_output = bool((doc.get("deepseek") or {}).get("used"))
        if (
            status in {"clean", "clean_with_redactions", "extracted", "review"}
            or (has_model_output and quality_state in {"pass", "review"})
        ):
            facts[path.parent.name] = doc
    return facts


def _active_subscription_slugs(out_root: Path) -> list[str]:
    index = _read_json(out_root / "issues" / "index.json")
    rows = index.get("items") or []
    return sorted(
        row["slug"]
        for row in rows
        if row.get("slug") and row.get("status") in {"Open", "Upcoming"}
    )


def _existing_quality(out_root: Path, slug: str) -> str:
    path = out_root / "issues" / "by-slug" / f"{slug}.json"
    if not path.exists():
        return "review"
    return ((json.loads(path.read_text(encoding="utf-8")).get("data_quality") or {}).get("state")) or "review"


def _meta_from_existing_manifest(out_root: Path) -> dict[str, Any]:
    manifest = _read_json(out_root / "manifest.json")
    return {
        key: manifest[key]
        for key in ("schema_version", "dataset_version", "source_dataset_version", "generated_at", "generated_by", "public_contract")
        if key in manifest
    }


def _update_subscription_summaries(root: Path, updates: dict[str, dict[str, Any]]) -> int:
    updated = 0
    for path in [
        root / "issues" / "index.json",
        *sorted((root / "issues" / "by-status").glob("*.json")),
        *sorted((root / "issues" / "by-year").glob("*.json")),
        *sorted((root / "issues" / "by-kind").glob("*.json")),
        *sorted((root / "companies" / "by-slug").glob("*.json")),
    ]:
        if not path.exists():
            continue
        doc = _read_json(path)
        changed = _replace_summary_items(doc, updates)
        if changed:
            updated += _write_json_if_changed(path, doc)
    return updated


def _replace_summary_items(value: Any, updates: dict[str, dict[str, Any]]) -> bool:
    changed = False
    if isinstance(value, dict):
        items = value.get("items")
        if isinstance(items, list):
            for idx, row in enumerate(items):
                if isinstance(row, dict) and row.get("slug") in updates:
                    items[idx] = updates[row["slug"]]
                    changed = True
        issues = value.get("issues")
        if isinstance(issues, list):
            for idx, row in enumerate(issues):
                if isinstance(row, dict) and row.get("slug") in updates:
                    issues[idx] = updates[row["slug"]]
                    changed = True
    return changed


def _index_doc(meta: dict[str, Any], schema_name: str, items: list[dict[str, Any]], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    doc = {**meta, "$schema": f"https://ipo-watch.local/schema/v3/{schema_name}", "count": len(items), "items": items}
    if extra:
        doc.update(extra)
    return doc


def _quality(slug: str | None, validation: dict[str, Any]) -> dict[str, Any]:
    findings = validation["findings_by_slug"].get(slug, {})
    return {"state": validation["states"].get(slug, "review"), "errors": findings.get("blocking", []), "warnings": findings.get("review", []), "info": findings.get("info", [])}


def _schema_url(name: str) -> str:
    return f"https://ipo-watch.local/schema/v3/{name}.schema.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_json_if_changed(path: Path, doc: Any) -> int:
    body = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == body:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return 1


def _rewrite_urls(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _rewrite_urls(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_rewrite_urls(v) for v in value]
    if isinstance(value, str):
        if value.startswith("/ipo/"):
            return "/ipos/" + value[len("/ipo/"):]
        if value.startswith("/company/"):
            return "/companies/" + value[len("/company/"):]
        return value.replace("data/site_v2", "data/ipo_watch_v3")
    return value


def _latest_snapshot_at(snapshots: list[dict[str, Any]]) -> str | None:
    values = []
    for snap in snapshots:
        fetched_at = (snap.get("meta") or {}).get("fetched_at")
        if fetched_at:
            try:
                values.append(datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00")))
            except ValueError:
                pass
    return max(values).astimezone(timezone.utc).replace(microsecond=0).isoformat() if values else None


def _dataset_version(generated_at: str, source_version: str | None) -> str:
    if source_version and source_version.startswith("v202"):
        return "v3." + source_version[1:]
    try:
        dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        return f"v3.{dt.strftime('%Y.%m.%d-%H%M')}"
    except ValueError:
        return "v3.unknown"


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _finding(kind: str, message: str) -> dict[str, str]:
    return {"class": kind, "message": message}


def _valid_isin(value: str) -> bool:
    return len(value) == 12 and value.startswith("IN") and value.isalnum() and value.upper() == value


def _is_name_only(record: dict[str, Any]) -> bool:
    pricing = record.get("pricing") or {}
    timeline = record.get("timeline") or {}
    docs = record.get("documents") or {}
    sub = record.get("subscription") or {}
    perf = record.get("listing_performance") or {}
    return not any([docs, pricing.get("issue_price_paise"), pricing.get("price_band_upper_paise"), timeline.get("open_date"), timeline.get("close_date"), timeline.get("listing_date"), sub.get("overall_times_x"), perf.get("listing_close_price_paise"), perf.get("current_price_paise")])


def _bps(base: Any, value: Any) -> int | None:
    if not isinstance(base, int) or not isinstance(value, int) or base <= 0:
        return None
    return int(((Decimal(value - base) / Decimal(base)) * Decimal(10000)).to_integral_value())


def _cagr_bps(base: Any, value: Any, listing_date: Any) -> int | None:
    start = _date(listing_date)
    if not start or not isinstance(base, int) or not isinstance(value, int) or base <= 0 or value <= 0:
        return None
    years = Decimal((TODAY - start).days) / Decimal("365.25")
    if years < 1:
        return None
    return int((((Decimal(value) / Decimal(base)) ** (Decimal(1) / years) - Decimal(1)) * Decimal(10000)).to_integral_value())


def _market_source(record: dict[str, Any]) -> dict[str, Any]:
    prov = record.get("field_provenance") or {}
    for field in ("listing_performance.current_price_paise", "listing_performance.listing_close_price_paise", "listing_performance.listing_open_price_paise"):
        source = (prov.get(field) or {}).get("source")
        if source:
            return {"name": source, "field": field}
    return {"name": None, "field": None}


def _market_quality(record: dict[str, Any]) -> dict[str, str]:
    source = _market_source(record).get("name")
    if source == "kite":
        return {"confidence": "high", "reason": "Kite market data snapshot"}
    if source == "yahoo":
        return {"confidence": "medium", "reason": "Yahoo Finance fallback snapshot"}
    if source in {"bse", "nse"}:
        return {"confidence": "medium", "reason": "Exchange public feed"}
    return {"confidence": "missing", "reason": "No market price source"}
