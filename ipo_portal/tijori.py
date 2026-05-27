from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .normalize_v2.identity import normalize_name
from .storage import write_json


TIJORI_IPO_URL = "https://b2b.tijorifinance.com/b2b/v1/in/api/kite-screener/ipo/"


@dataclass(frozen=True)
class TijoriStats:
    rows: int
    with_isin: int
    with_symbol: int
    with_financials: int
    with_revenue_mix: int
    with_peers: int
    with_shareholding: int


def fetch_tijori_ipo_feed(url: str = TIJORI_IPO_URL, timeout: int = 120) -> list[dict[str, Any]]:
    response = requests.get(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "IPO-Watch/1.0 (+https://ipowatch.co)",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError(f"Tijori IPO feed returned {type(payload).__name__}, expected list")
    return [row for row in payload if isinstance(row, dict)]


def feed_stats(rows: list[dict[str, Any]]) -> TijoriStats:
    return TijoriStats(
        rows=len(rows),
        with_isin=sum(1 for row in rows if row.get("isin")),
        with_symbol=sum(1 for row in rows if _keystats(row).get("symbol")),
        with_financials=sum(1 for row in rows if (_keystats(row).get("financials") or {}).get("yearly_results")),
        with_revenue_mix=sum(1 for row in rows if ((_revenue_mix(row).get("revenue_mix") or {}).get("latest_data"))),
        with_peers=sum(1 for row in rows if row.get("peers")),
        with_shareholding=sum(1 for row in rows if row.get("shareholding")),
    )


def write_tijori_enrichment(rows: list[dict[str, Any]], out_path: Path) -> dict[str, Any]:
    items: dict[str, dict[str, Any]] = {}
    for row in rows:
        company_name = str(row.get("compname") or "").strip()
        key = normalize_name(company_name)
        if not key:
            continue
        stats = _keystats(row)
        revenue_mix = _revenue_mix(row)
        items[key] = {
            "company_name": company_name,
            "normalized_name": key,
            "isin": row.get("isin"),
            "symbol": stats.get("symbol"),
            "sector": stats.get("sector"),
            "details": stats.get("details"),
            "ipo_size": stats.get("ipo_size"),
            "market_cap": stats.get("market_cap"),
            "pe": stats.get("pe"),
            "pb": stats.get("pb"),
            "sector_pe": stats.get("sector_pe"),
            "sector_pb": stats.get("sector_pb"),
            "business_perc": stats.get("business_perc"),
            "existing_perc": stats.get("existing_perc"),
            "business_value": stats.get("business_value"),
            "existing_value": stats.get("existing_value"),
            "financials": stats.get("financials") or {},
            "revenue_mix": revenue_mix,
            "peers": row.get("peers") or [],
            "shareholding": row.get("shareholding") or {},
            "source": "tijori-kite-screener",
        }
    doc = {
        "source": "tijori-kite-screener",
        "source_url": TIJORI_IPO_URL,
        "stats": feed_stats(rows).__dict__,
        "items": items,
    }
    write_json(out_path, doc)
    return doc


def write_sector_map_from_tijori(enrichment: dict[str, Any], out_path: Path) -> dict[str, Any]:
    items = enrichment.get("items") or {}
    sector_map = {}
    for key, row in items.items():
        sector = row.get("sector")
        if not sector:
            continue
        sector_map[key] = {
            "sector": sector,
            "industry": sector,
            "sub_industry": None,
            "source": "tijori-kite-screener",
            "isin": row.get("isin"),
            "symbol": row.get("symbol"),
        }
    write_json(out_path, sector_map)
    return sector_map


def _keystats(row: dict[str, Any]) -> dict[str, Any]:
    stats = row.get("keystats")
    return stats if isinstance(stats, dict) else {}


def _revenue_mix(row: dict[str, Any]) -> dict[str, Any]:
    revenue_mix = row.get("revenue_mix")
    return revenue_mix if isinstance(revenue_mix, dict) else {}
