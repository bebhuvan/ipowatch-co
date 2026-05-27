from __future__ import annotations

import json
import math
import sqlite3
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any

from .storage import write_json


PAGE_SIZE = 50


def export_performance_site_data(site_root: Path, issues: list[dict[str, Any]], page_size: int = PAGE_SIZE) -> dict[str, Any]:
    rows = build_performance_rows(issues, kite_db_path=site_root.parent / "private" / "kite" / "kite.sqlite")
    pages = [rows[i : i + page_size] for i in range(0, len(rows), page_size)]
    page_count = max(1, len(pages))
    summary = build_summary(rows, page_size, page_count)

    write_json(site_root / "performance" / "summary.json", summary)
    write_json(site_root / "performance" / "index.json", {"schema_version": "1.0.0", "rows": rows})
    for idx, page_rows in enumerate(pages or [[]], start=1):
        write_json(
            site_root / "performance" / "pages" / f"page-{idx}.json",
            {
                "schema_version": "1.0.0",
                "page": idx,
                "page_size": page_size,
                "page_count": page_count,
                "total_rows": len(rows),
                "rows": page_rows,
            },
        )
    return summary


def build_performance_rows(issues: list[dict[str, Any]], kite_db_path: Path | None = None) -> list[dict[str, Any]]:
    overlays = load_kite_overlays(kite_db_path) if kite_db_path else {}
    benchmarks = load_benchmark_prices(kite_db_path) if kite_db_path else {}
    unique: dict[str, dict[str, Any]] = {}
    for issue in issues:
        if normalize_issue_type(issue["classification"].get("issue_type")) != "ipo":
            continue
        if is_probable_debt_public_issue(issue):
            continue
        listing = issue.get("listing_performance") or {}
        pricing = issue.get("pricing") or {}
        issue_price = issue_price_for_performance(pricing)
        source_open = coerce_number(listing.get("listing_day_open"))
        source_close = coerce_number(listing.get("listing_day_close"))
        source_current = coerce_number(listing.get("current_price"))
        listing_date = listing_date_for_performance(issue)
        if issue_price is None or (source_open is None and source_close is None and source_current is None and listing_date is None):
            continue

        overlay = overlays.get(issue["slug"], {})
        kite_listing = overlay.get("listing") or {}
        kite_current = overlay.get("current") or {}
        listing_open = coerce_number(kite_listing.get("open"))
        if listing_open is None:
            listing_open = source_open
        listing_close = coerce_number(kite_listing.get("close"))
        if listing_close is None:
            listing_close = source_close
        current_price = coerce_number(kite_current.get("last_price"))
        if current_price is None:
            current_price = source_current
        benchmark_returns = benchmark_returns_for_listing(benchmarks, listing_date)

        row = {
            "id": issue.get("id"),
            "slug": issue["slug"],
            "url_path": issue["url_path"],
            "company_name": issue["company"]["name"],
            "symbol": issue["company"].get("symbol"),
            "issue_type": issue["classification"].get("issue_type"),
            "exchange_platform": issue["classification"].get("exchange_platform"),
            "listing_date": listing_date,
            "open_date": issue["timeline"].get("open_date"),
            "close_date": issue["timeline"].get("close_date"),
            "issue_price": issue_price,
            "listing_open": listing_open,
            "listing_close": listing_close,
            "current_price": current_price,
            "listing_open_return": percent_change(issue_price, listing_open),
            "listing_close_return": percent_change(issue_price, listing_close),
            "current_return": percent_change(issue_price, current_price),
            "benchmarks": benchmark_returns,
            "source_listing_open": source_open,
            "source_listing_close": source_close,
            "source_current_price": source_current,
            "listing_price_source": "kite" if kite_listing.get("status") in {"ok", "shifted"} and (kite_listing.get("open") is not None or kite_listing.get("close") is not None) else "source",
            "current_price_source": "kite" if kite_current.get("last_price") is not None else "source",
            "kite": {
                "tradingsymbol": overlay.get("tradingsymbol"),
                "exchange": overlay.get("exchange"),
                "instrument_token": overlay.get("instrument_token"),
                "listing_status": kite_listing.get("status"),
                "listing_candle_date": kite_listing.get("candle_date"),
                "current_fetched_at": kite_current.get("fetched_at"),
            },
        }
        key = performance_key(row)
        if key not in unique or row_score(row) > row_score(unique[key]):
            unique[key] = row

    return sorted(unique.values(), key=lambda row: (row.get("listing_date") or row.get("close_date") or "", row.get("company_name") or ""), reverse=True)


def build_summary(rows: list[dict[str, Any]], page_size: int, page_count: int) -> dict[str, Any]:
    years: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        year = (row.get("listing_date") or row.get("close_date") or "undated")[:4]
        years[year].append(row)

    year_rows = []
    for year, bucket in sorted(years.items(), reverse=True):
        day_returns = [r["listing_close_return"] for r in bucket if r.get("listing_close_return") is not None]
        current_returns = [r["current_return"] for r in bucket if r.get("current_return") is not None]
        year_rows.append(
            {
                "year": year,
                "count": len(bucket),
                "with_current": len(current_returns),
                "median_day_one": median(day_returns) if day_returns else None,
                "median_current": median(current_returns) if current_returns else None,
                "positive_day_one_pct": ratio(sum(1 for v in day_returns if v > 0), len(day_returns)),
            }
        )

    current_rows = [r for r in rows if r.get("current_return") is not None]
    day_rows = [r for r in rows if r.get("listing_close_return") is not None]
    return {
        "schema_version": "1.0.0",
        "total_rows": len(rows),
        "page_size": page_size,
        "page_count": page_count,
        "with_kite_listing": sum(1 for r in rows if r.get("listing_price_source") == "kite"),
        "with_kite_current": sum(1 for r in rows if r.get("current_price_source") == "kite"),
        "with_day_one": len(day_rows),
        "with_current": len(current_rows),
        "years": year_rows,
        "source_mix": dict(Counter(r.get("listing_price_source") or "source" for r in rows)),
        "benchmarks": build_benchmark_summary(current_rows),
        "best_since_issue": top_rows(current_rows, "current_return", reverse=True, limit=8),
        "worst_since_issue": top_rows(current_rows, "current_return", reverse=False, limit=8),
        "best_day_one": top_rows(day_rows, "listing_close_return", reverse=True, limit=8),
        "worst_day_one": top_rows(day_rows, "listing_close_return", reverse=False, limit=8),
        "return_buckets": return_buckets(current_rows, "current_return"),
    }


def load_kite_overlays(db_path: Path) -> dict[str, dict[str, Any]]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        overlays: dict[str, dict[str, Any]] = {}
        for row in conn.execute("select issue_slug, kite_exchange, tradingsymbol, instrument_token from ipo_symbol_map where instrument_token is not null"):
            overlays.setdefault(row["issue_slug"], {}).update(
                {
                    "exchange": row["kite_exchange"],
                    "tradingsymbol": row["tradingsymbol"],
                    "instrument_token": row["instrument_token"],
                }
            )
        for row in conn.execute("select * from kite_listing_prices"):
            overlays.setdefault(row["issue_slug"], {})["listing"] = {
                "status": row["status"],
                "candle_date": row["candle_date"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "fetched_at": row["fetched_at"],
            }
        for row in conn.execute("select * from kite_current_prices"):
            overlays.setdefault(row["issue_slug"], {})["current"] = {
                "last_price": row["last_price"],
                "fetched_at": row["fetched_at"],
            }
        return overlays
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


def load_benchmark_prices(db_path: Path) -> dict[str, dict[str, Any]]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        benchmarks: dict[str, dict[str, Any]] = {}
        for row in conn.execute(
            """
            select benchmark_key, benchmark_label, candle_date, close
            from kite_benchmark_prices
            order by benchmark_key, candle_date
            """
        ):
            bucket = benchmarks.setdefault(row["benchmark_key"], {"label": row["benchmark_label"], "dates": [], "closes": []})
            bucket["dates"].append(row["candle_date"])
            bucket["closes"].append(row["close"])
        return benchmarks
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


def benchmark_returns_for_listing(benchmarks: dict[str, dict[str, Any]], listing_date: str | None) -> dict[str, dict[str, Any]]:
    if not listing_date:
        return {}
    result = {}
    for key, benchmark in benchmarks.items():
        dates = benchmark.get("dates") or []
        closes = benchmark.get("closes") or []
        idx = bisect_left(dates, listing_date)
        if idx >= len(dates) or not closes:
            continue
        start = closes[idx]
        end = closes[-1]
        result[key] = {
            "label": benchmark.get("label"),
            "start_date": dates[idx],
            "end_date": dates[-1],
            "return": percent_change(start, end),
        }
    return result


def build_benchmark_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys: dict[str, str] = {}
    for row in rows:
        for key, benchmark in (row.get("benchmarks") or {}).items():
            keys[key] = benchmark.get("label") or key

    summaries = []
    for key, label in sorted(keys.items()):
        comparable = []
        for row in rows:
            ipo_return = row.get("current_return")
            benchmark_return = ((row.get("benchmarks") or {}).get(key) or {}).get("return")
            if ipo_return is None or benchmark_return is None:
                continue
            comparable.append({**row, "benchmark_return": benchmark_return, "excess_return": ipo_return - benchmark_return})
        excess = [row["excess_return"] for row in comparable]
        summaries.append(
            {
                "key": key,
                "label": label,
                "with_comparison": len(comparable),
                "beat_count": sum(1 for row in comparable if row["excess_return"] > 0),
                "beat_pct": ratio(sum(1 for row in comparable if row["excess_return"] > 0), len(comparable)),
                "median_excess": median(excess) if excess else None,
                "best_excess": compact_benchmark_row(max(comparable, key=lambda row: row["excess_return"])) if comparable else None,
                "worst_excess": compact_benchmark_row(min(comparable, key=lambda row: row["excess_return"])) if comparable else None,
            }
        )
    return summaries


def compact_benchmark_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": row["slug"],
        "url_path": row["url_path"],
        "company_name": row["company_name"],
        "listing_date": row.get("listing_date"),
        "current_return": row.get("current_return"),
        "benchmark_return": row.get("benchmark_return"),
        "excess_return": row.get("excess_return"),
    }


def performance_key(row: dict[str, Any]) -> str:
    event_year = (row.get("open_date") or row.get("close_date") or row.get("listing_date") or "")[:4]
    return "|".join(
        [
            normalize_name(row.get("company_name")),
            event_year,
            str(row.get("issue_price") or ""),
        ]
    )


def row_score(row: dict[str, Any]) -> int:
    return (
        (4 if row.get("listing_price_source") == "kite" else 0)
        + (4 if row.get("current_price_source") == "kite" else 0)
        + (2 if row.get("current_price") is not None else 0)
        + (1 if row.get("listing_close") is not None else 0)
    )


def normalize_issue_type(value: str | None) -> str:
    return (value or "").lower().replace(" ", "").replace("_", "").replace("-", "")


def normalize_name(value: str | None) -> str:
    return (value or "").lower().replace("&", " and ").replace("limited", "").replace("ltd", "").replace(".", "").strip()


def coerce_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").strip()
        try:
            parsed = float(cleaned)
            return parsed if math.isfinite(parsed) else None
        except ValueError:
            return None
    return None


def issue_price_for_performance(pricing: dict[str, Any]) -> float | None:
    issue_price = coerce_number(pricing.get("issue_price"))
    band = pricing.get("price_band") or {}
    low = coerce_number(band.get("min"))
    high = coerce_number(band.get("max"))
    if issue_price is not None:
        if low is not None and high is not None and not low <= issue_price <= high:
            return high
        return issue_price

    if low is not None and high is not None and low == high:
        return high
    if low is None and high is not None:
        return high
    return None


def listing_date_for_performance(issue: dict[str, Any]) -> str | None:
    timeline = issue.get("timeline") or {}
    listing_date = timeline.get("listing_date")
    close_date = timeline.get("close_date")
    if not listing_date or not close_date:
        return listing_date
    try:
        listed = date.fromisoformat(listing_date)
        closed = date.fromisoformat(close_date)
    except ValueError:
        return listing_date
    if (listed - closed).days > 180:
        return None
    return listing_date


def is_probable_debt_public_issue(issue: dict[str, Any]) -> bool:
    pricing = issue.get("pricing") or {}
    band = pricing.get("price_band") or {}
    listing = issue.get("listing_performance") or {}
    sources = issue.get("sources") or []
    has_listing_prices = any(listing.get(field) is not None for field in ("listing_day_open", "listing_day_close", "current_price"))
    source_endpoints = {source.get("endpoint") for source in sources}
    return (
        "ipo_public_past_issues" in source_endpoints
        and coerce_number(band.get("min")) == 1000
        and coerce_number(band.get("max")) == 1000
        and not has_listing_prices
    )


def percent_change(start: float | None, end: float | None) -> float | None:
    if start is None or end is None or start == 0:
        return None
    return ((end - start) / start) * 100


def ratio(num: int, den: int) -> float | None:
    return (num / den) * 100 if den else None


def top_rows(rows: list[dict[str, Any]], field: str, reverse: bool, limit: int) -> list[dict[str, Any]]:
    trimmed = sorted(rows, key=lambda row: row.get(field) if row.get(field) is not None else (-math.inf if reverse else math.inf), reverse=reverse)[:limit]
    return [compact_row(row) for row in trimmed]


def compact_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": row["slug"],
        "url_path": row["url_path"],
        "company_name": row["company_name"],
        "listing_date": row.get("listing_date"),
        "issue_price": row.get("issue_price"),
        "listing_close": row.get("listing_close"),
        "current_price": row.get("current_price"),
        "listing_close_return": row.get("listing_close_return"),
        "current_return": row.get("current_return"),
        "listing_price_source": row.get("listing_price_source"),
        "current_price_source": row.get("current_price_source"),
    }


def return_buckets(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    edges = [(-math.inf, -75), (-75, -50), (-50, -25), (-25, 0), (0, 25), (25, 50), (50, 100), (100, 250), (250, math.inf)]
    labels = ["<-75", "-75 to -50", "-50 to -25", "-25 to 0", "0 to 25", "25 to 50", "50 to 100", "100 to 250", ">250"]
    buckets = [{"label": label, "count": 0} for label in labels]
    for row in rows:
        value = row.get(field)
        if value is None:
            continue
        for idx, (lo, hi) in enumerate(edges):
            if lo <= value < hi:
                buckets[idx]["count"] += 1
                break
    return buckets
