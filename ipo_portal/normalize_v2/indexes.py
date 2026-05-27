"""Build aggregation indexes over the v2 issue records.

After the pipeline writes ``data/site_v2/issues/by-slug/<slug>.json``,
this module emits the aggregate views the Astro site consumes:

* ``issues/index.json``            — flat list of compact issue summaries
* ``issues/by-year/<YYYY>.json``   — issues grouped by listing year
* ``issues/by-status/<state>.json``— grouped by status enum value
* ``issues/by-kind/<kind>.json``   — grouped by issue_type enum value
* ``companies/index.json``         — flat list of companies
* ``companies/by-slug/<slug>.json``— per-company record with its issues

Each index carries the standard metadata envelope so a consumer can read
one index file in isolation and know what it is. Indexes hold **compact
summaries** (not full records) to keep payloads small — a consumer
follows ``url_path`` to the full by-slug record for detail.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..storage import write_json
from ..orchestrator.metadata import build_envelope, utc_now_iso


# Fields lifted into the compact summary that lists/cards render from.
def _summarize(doc: dict[str, Any]) -> dict[str, Any]:
    identity = doc.get("identity") or {}
    pricing = doc.get("pricing") or {}
    timeline = doc.get("timeline") or {}
    perf = doc.get("listing_performance") or {}
    subs = doc.get("subscription") or {}
    return {
        "slug": doc.get("slug") or identity.get("slug"),
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
        "url_path": f"/ipo/{doc.get('slug') or identity.get('slug')}/",
        "data_quality_state": (doc.get("data_quality") or {}).get("state"),
        "source_count": len(doc.get("sources") or []),
    }


def _year_of(summary: dict[str, Any]) -> str:
    for key in ("listing_date", "close_date", "open_date"):
        val = summary.get(key)
        if val and len(str(val)) >= 4:
            return str(val)[:4]
    return "undated"


def build_indexes(out_root: Path) -> dict[str, int]:
    """Read every by-slug record and emit all aggregation indexes.

    Returns a small stats dict for the manifest.
    """
    by_slug_dir = out_root / "issues" / "by-slug"
    if not by_slug_dir.exists():
        return {"issues": 0, "companies": 0}

    summaries: list[dict[str, Any]] = []
    for path in sorted(by_slug_dir.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        summaries.append(_summarize(doc))

    summaries.sort(key=lambda s: (s.get("listing_date") or s.get("close_date") or "", s.get("company_name") or ""), reverse=True)

    _write_flat_index(out_root, summaries)
    _write_by_year(out_root, summaries)
    _write_by_status(out_root, summaries)
    _write_by_kind(out_root, summaries)
    company_count = _write_companies(out_root, summaries)

    return {"issues": len(summaries), "companies": company_count}


def _index_doc(schema_name: str, items: list[dict[str, Any]], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    env = build_envelope(
        schema_name=schema_name,
        schema_version="2.0.0",
        notes="Aggregation index of compact issue summaries. Follow url_path for full records.",
    )
    doc = {**env, "generated_at": utc_now_iso(), "count": len(items), "items": items}
    if extra:
        doc.update(extra)
    return doc


def _write_flat_index(out_root: Path, summaries: list[dict[str, Any]]) -> None:
    write_json(
        out_root / "issues" / "index.json",
        _index_doc("issues/index.schema", summaries),
    )


def _write_by_year(out_root: Path, summaries: list[dict[str, Any]]) -> None:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in summaries:
        buckets[_year_of(s)].append(s)
    year_dir = out_root / "issues" / "by-year"
    for year, items in buckets.items():
        write_json(year_dir / f"{year}.json", _index_doc("issues/by-year.schema", items, {"year": year}))
    # A small directory listing of available years + counts.
    write_json(
        year_dir / "index.json",
        _index_doc(
            "issues/by-year-index.schema",
            [{"year": y, "count": len(items)} for y, items in sorted(buckets.items(), reverse=True)],
        ),
    )


def _write_by_status(out_root: Path, summaries: list[dict[str, Any]]) -> None:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in summaries:
        buckets[(s.get("status") or "unknown").lower()].append(s)
    status_dir = out_root / "issues" / "by-status"
    for status, items in buckets.items():
        write_json(status_dir / f"{status}.json", _index_doc("issues/by-status.schema", items, {"status": status}))


def _write_by_kind(out_root: Path, summaries: list[dict[str, Any]]) -> None:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in summaries:
        kind = (s.get("issue_type") or "unknown").lower().replace(" ", "_")
        buckets[kind].append(s)
    kind_dir = out_root / "issues" / "by-kind"
    for kind, items in buckets.items():
        write_json(kind_dir / f"{kind}.json", _index_doc("issues/by-kind.schema", items, {"kind": kind}))


def _write_companies(out_root: Path, summaries: list[dict[str, Any]]) -> int:
    """Group issues by company (normalized name) and emit company records.

    A company can have multiple issues (IPO then FPO then buyback). The
    company slug is the issue slug's name-stem without the issue's
    short-id; we use the company_name normalized form as the grouping key
    and the most-recent issue's slug-stem for the company slug.
    """
    from .identity import normalize_name, short_id, slugify

    by_company: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in summaries:
        name = s.get("company_name") or ""
        key = normalize_name(name)
        if not key:
            continue
        by_company[key].append(s)

    company_index: list[dict[str, Any]] = []
    by_slug_dir = out_root / "companies" / "by-slug"
    written: set[str] = set()
    for norm_name, issues in by_company.items():
        company_slug = slugify(norm_name, f"company:{norm_name}")
        written.add(company_slug)
        # Newest issue first.
        issues_sorted = sorted(
            issues,
            key=lambda s: (s.get("listing_date") or s.get("close_date") or ""),
            reverse=True,
        )
        display_name = issues_sorted[0].get("company_name")
        record = {
            **build_envelope(
                schema_name="company.schema",
                schema_version="2.0.0",
                notes="Company aggregate — all issues by this entity.",
            ),
            "slug": company_slug,
            "company_name": display_name,
            "normalized_name": norm_name,
            "url_path": f"/company/{company_slug}/",
            "issue_count": len(issues_sorted),
            "issues": issues_sorted,
        }
        write_json(by_slug_dir / f"{company_slug}.json", record)
        company_index.append(
            {
                "slug": company_slug,
                "company_name": display_name,
                "issue_count": len(issues_sorted),
                "url_path": f"/company/{company_slug}/",
            }
        )

    # Prune stale company files: a company slug can disappear between runs
    # (name cleanup, re-consolidation) leaving an orphan that would inflate
    # the on-disk count past the manifest's.
    if by_slug_dir.is_dir():
        for path in by_slug_dir.glob("*.json"):
            if path.stem not in written:
                path.unlink()

    company_index.sort(key=lambda c: c.get("company_name") or "")
    write_json(
        out_root / "companies" / "index.json",
        _index_doc("companies/index.schema", company_index),
    )
    return len(company_index)
