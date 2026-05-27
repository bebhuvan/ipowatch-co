"""Fetch Yahoo Finance listing/current prices into a v2 raw snapshot.

Yahoo is used as a public fallback for post-listing analytics when the
exchange/BSE performance feeds or Kite cache do not have a fresh current
price. The output intentionally goes through ``data/raw/yahoo/performance``
so normalization, provenance, and audit checks remain the single path to
published data.
"""

from __future__ import annotations

import json
import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote
import re

import requests

from .storage import save_raw_snapshot


DEFAULT_SITE_ROOT = Path("data/site_v2")
DEFAULT_DATA_ROOT = Path("data")
DEFAULT_CACHE_DIR = Path("data/cache/yahoo")
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
BSE_STOCK_CODE_RE = re.compile(r"/(\d{5,6})/?$")


@dataclass(frozen=True)
class Candidate:
    slug: str
    company_name: str
    symbol: str
    yahoo_symbol: str
    listing_date: str
    issue_price_paise: int
    board_type: str | None


def _paise(rupees: float | int | Decimal | None) -> int | None:
    if rupees is None:
        return None
    return int((Decimal(str(rupees)) * 100).to_integral_value())


def _bps(price_paise: int | None, issue_price_paise: int | None) -> int | None:
    if price_paise is None or issue_price_paise is None or issue_price_paise <= 0:
        return None
    return int(((Decimal(price_paise - issue_price_paise) / Decimal(issue_price_paise)) * Decimal(10000)).to_integral_value())


def _epoch(date_text: str, *, days: int = 0) -> int:
    dt = datetime.fromisoformat(date_text).replace(tzinfo=timezone.utc) + timedelta(days=days)
    return int(dt.timestamp())


def _read_issues(site_root: Path) -> list[dict[str, Any]]:
    by_slug = site_root / "issues" / "by-slug"
    if not by_slug.exists():
        raise FileNotFoundError(f"missing {by_slug}; run normalize first")
    out: list[dict[str, Any]] = []
    for path in sorted(by_slug.glob("*.json")):
        out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


def candidates(site_root: Path = DEFAULT_SITE_ROOT) -> list[Candidate]:
    """Return IPOs that can be queried on Yahoo from current canonical data."""
    rows: list[Candidate] = []
    for issue in _read_issues(site_root):
        identity = issue.get("identity") or {}
        pricing = issue.get("pricing") or {}
        timeline = issue.get("timeline") or {}
        if identity.get("issue_type") != "IPO":
            continue
        symbol = identity.get("symbol")
        listing_date = timeline.get("listing_date")
        issue_price_paise = pricing.get("issue_price_paise")
        if not symbol or not listing_date or not isinstance(issue_price_paise, int):
            continue
        symbol_text = str(symbol).strip().upper()
        if not symbol_text:
            continue
        bse_code = _bse_code(identity.get("aliases") or [])
        if identity.get("board_type") == "SME Board" and bse_code:
            yahoo_symbol = f"{bse_code}.BO"
        else:
            yahoo_symbol = f"{symbol_text}.NS"
        rows.append(
            Candidate(
                slug=issue["slug"],
                company_name=identity.get("company_name") or issue["slug"],
                symbol=symbol_text,
                yahoo_symbol=yahoo_symbol,
                listing_date=listing_date,
                issue_price_paise=issue_price_paise,
                board_type=identity.get("board_type"),
            )
        )
    return rows


def _bse_code(aliases: list[Any]) -> str | None:
    for alias in aliases:
        if not isinstance(alias, str) or not alias.startswith("bse:stock_page:"):
            continue
        match = BSE_STOCK_CODE_RE.search(alias)
        if match:
            return match.group(1)
    return None


def fetch_chart(session: requests.Session, yahoo_symbol: str, listing_date: str, timeout: float = 12.0, retries: int = 2) -> dict[str, Any]:
    params = {
        "period1": str(_epoch(listing_date, days=-5)),
        "period2": str(_epoch(datetime.now(timezone.utc).date().isoformat(), days=1)),
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }
    url = YAHOO_CHART_URL.format(symbol=quote(yahoo_symbol, safe=""))
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError, json.JSONDecodeError) as exc:
            last_error = exc
            if isinstance(exc, requests.HTTPError):
                status = exc.response.status_code if exc.response is not None else None
                if status is not None and 400 <= status < 500 and status not in {408, 425, 429}:
                    raise
            if attempt < retries:
                time.sleep(0.75 * (2 ** attempt))
    assert last_error is not None
    raise last_error


def parse_chart(payload: dict[str, Any], listing_date: str) -> dict[str, Any]:
    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not isinstance(result, dict):
        return {"status": "no_result"}
    timestamps = result.get("timestamp") or []
    quote_rows = (((result.get("indicators") or {}).get("quote") or [None])[0]) or {}
    opens = quote_rows.get("open") or []
    closes = quote_rows.get("close") or []
    meta = result.get("meta") or {}

    listing_open = None
    listing_close = None
    listing_candle_date = None
    for idx, ts in enumerate(timestamps):
        candle_date = datetime.fromtimestamp(int(ts), timezone.utc).date().isoformat()
        if candle_date < listing_date:
            continue
        open_value = opens[idx] if idx < len(opens) else None
        close_value = closes[idx] if idx < len(closes) else None
        if open_value is None and close_value is None:
            continue
        listing_open = open_value
        listing_close = close_value
        listing_candle_date = candle_date
        break

    current = meta.get("regularMarketPrice")
    if current is None:
        for value in reversed(closes):
            if value is not None:
                current = value
                break

    return {
        "status": "ok" if (listing_open is not None or listing_close is not None or current is not None) else "no_prices",
        "currency": meta.get("currency"),
        "exchange_name": meta.get("exchangeName"),
        "instrument_type": meta.get("instrumentType"),
        "listing_candle_date": listing_candle_date,
        "listing_open_paise": _paise(listing_open),
        "listing_close_paise": _paise(listing_close),
        "current_price_paise": _paise(current),
        "regular_market_time": datetime.fromtimestamp(int(meta["regularMarketTime"]), timezone.utc).isoformat()
        if meta.get("regularMarketTime") else None,
    }


def build_rows(
    site_root: Path = DEFAULT_SITE_ROOT,
    *,
    limit: int | None = None,
    sleep_seconds: float = 0.15,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    cache_max_age_hours: float = 18.0,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    session = session or requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 IPO-Watch/0.1 (+https://ipo-watch.local)",
        "Accept": "application/json,text/plain,*/*",
    }
    session.headers.update(headers)

    for idx, candidate in enumerate(candidates(site_root)):
        if limit is not None and idx >= limit:
            break
        row: dict[str, Any] = {
            "slug": candidate.slug,
            "company_name": candidate.company_name,
            "symbol": candidate.symbol,
            "yahoo_symbol": candidate.yahoo_symbol,
            "listing_date": candidate.listing_date,
            "issue_price_paise": candidate.issue_price_paise,
            "board_type": candidate.board_type,
        }
        cached = _cache_read(cache_dir, candidate, max_age_hours=cache_max_age_hours)
        if cached is not None:
            rows.append({**row, **cached, "cache": "hit"})
            continue
        try:
            parsed = parse_chart(fetch_chart(session, candidate.yahoo_symbol, candidate.listing_date), candidate.listing_date)
            row.update(parsed)
        except Exception as exc:  # noqa: BLE001 - raw snapshot should preserve fetch failures.
            row.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})

        row["listing_gain_bps"] = _bps(row.get("listing_close_paise"), candidate.issue_price_paise)
        row["current_gain_from_issue_bps"] = _bps(row.get("current_price_paise"), candidate.issue_price_paise)
        row["cache"] = "miss"
        _cache_write(cache_dir, candidate, row)
        rows.append(row)
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return rows


def export_snapshot(
    site_root: Path = DEFAULT_SITE_ROOT,
    data_root: Path = DEFAULT_DATA_ROOT,
    *,
    limit: int | None = None,
    sleep_seconds: float = 0.15,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    cache_max_age_hours: float = 18.0,
) -> Path:
    rows = build_rows(
        site_root=site_root,
        limit=limit,
        sleep_seconds=sleep_seconds,
        cache_dir=cache_dir,
        cache_max_age_hours=cache_max_age_hours,
    )
    return save_raw_snapshot(
        root=data_root,
        source="yahoo",
        endpoint_name="performance",
        url="https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        body=rows,
        fetched_at=datetime.now(timezone.utc).replace(microsecond=0),
        status_code=200,
    )


def _cache_key(candidate: Candidate) -> str:
    text = f"{candidate.yahoo_symbol}|{candidate.listing_date}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _cache_read(cache_dir: Path | None, candidate: Candidate, *, max_age_hours: float) -> dict[str, Any] | None:
    if cache_dir is None:
        return None
    path = cache_dir / f"{_cache_key(candidate)}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(payload.get("fetched_at"))
    except Exception:  # noqa: BLE001 - corrupt cache is just a miss.
        return None
    age = datetime.now(timezone.utc) - fetched_at.astimezone(timezone.utc)
    if age.total_seconds() > max_age_hours * 3600:
        return None
    row = payload.get("row")
    return row if isinstance(row, dict) else None


def _cache_write(cache_dir: Path | None, candidate: Candidate, row: dict[str, Any]) -> None:
    if cache_dir is None:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{_cache_key(candidate)}.json"
    payload = {
        "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "yahoo_symbol": candidate.yahoo_symbol,
        "listing_date": candidate.listing_date,
        "row": {k: v for k, v in row.items() if k not in {"slug", "company_name", "symbol", "board_type", "issue_price_paise", "cache"}},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
