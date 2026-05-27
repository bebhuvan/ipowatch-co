"""Compute listing-gain + current-performance from Kite data → v2 snapshot.

Reads the Kite price cache (``data/private/kite/kite.sqlite``) populated
by ``ipo_portal.kite`` (instruments → map → backfill-listings →
refresh-current) and computes, per IPO:

* ``listing_open_gain_bps``      — (listing_open − issue_price)/issue_price
* ``listing_gain_bps``           — (listing_close − issue_price)/issue_price
* ``current_gain_from_issue_bps``— (current − issue_price)/issue_price
* ``current_gain_from_listing_bps`` — (current − listing_close)/listing_close

All in integer basis points (1% = 100), money in paise — matching the v2
canonical conventions.

Output is a raw snapshot at ``data/raw/kite/performance/<ts>.json`` whose
rows carry ``company_name`` + ``listing_date`` + ``symbol``, so the v2
parser builds the same identity join key as every other source and the
values merge into the canonical issue record (Kite is the
``enrichment``-tier winner for ``current_price`` per
``SOURCE_PRECEDENCE.yaml``).

This keeps the gain math in one place and lets the listing-gain /
current-performance numbers flow through the normal normalize pipeline
rather than being bolted on.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .storage import save_raw_snapshot


DEFAULT_DB_PATH = Path("data/private/kite/kite.sqlite")


def _bps(numerator: float | None, denominator: float | None) -> int | None:
    """Return (numerator/denominator) as integer basis points, or None."""
    if numerator is None or not denominator:
        return None
    return int(round((numerator / denominator) * 10000))


def _paise(rupees: float | None) -> int | None:
    if rupees is None:
        return None
    return int((Decimal(str(rupees)) * 100).to_integral_value())


def compute_rows(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """Join the Kite tables and compute per-issue performance rows."""
    if not db_path.exists():
        raise FileNotFoundError(f"Kite DB not found at {db_path}; run the kite fetch first.")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Join listing candles by INSTRUMENT_TOKEN (not slug) so the
        # backfilled candles survive a v1→v2 slug cutover. One candle per
        # instrument (they're identical across slugs referencing it).
        # Current LTPs join by slug (re-fetched per slug scheme, cheap).
        rows = conn.execute(
            """
            select
              m.company_name, m.listing_date, m.issue_price,
              m.tradingsymbol, m.kite_exchange, m.exchange_platform,
              l.open as listing_open, l.close as listing_close,
              c.last_price as current_price
            from ipo_symbol_map m
            left join (
              select instrument_token, open, close
              from kite_listing_prices
              where status in ('ok','shifted') and instrument_token is not null
              group by instrument_token
            ) l on l.instrument_token = m.instrument_token
            left join kite_current_prices c on c.issue_slug = m.issue_slug and c.status = 'ok'
            where m.instrument_token is not null and m.issue_price is not null
            """
        ).fetchall()
    finally:
        conn.close()

    out: list[dict[str, Any]] = []
    for r in rows:
        issue_price = r["issue_price"]
        listing_open = r["listing_open"]
        listing_close = r["listing_close"]
        current_price = r["current_price"]
        if issue_price is None:
            continue
        if listing_open is None and listing_close is None and current_price is None:
            continue
        out.append(
            {
                "company_name": r["company_name"],
                "listing_date": r["listing_date"],
                "symbol": r["tradingsymbol"],
                "kite_exchange": r["kite_exchange"],
                "issue_price_paise": _paise(issue_price),
                "listing_open_paise": _paise(listing_open),
                "listing_close_paise": _paise(listing_close),
                "current_price_paise": _paise(current_price),
                "listing_open_gain_bps": _bps(
                    (listing_open - issue_price) if listing_open is not None else None,
                    issue_price,
                ),
                "listing_gain_bps": _bps(
                    (listing_close - issue_price) if listing_close is not None else None,
                    issue_price,
                ),
                "current_gain_from_issue_bps": _bps(
                    (current_price - issue_price) if current_price is not None else None,
                    issue_price,
                ),
                "current_gain_from_listing_bps": _bps(
                    (current_price - listing_close)
                    if (current_price is not None and listing_close is not None)
                    else None,
                    listing_close,
                ),
            }
        )
    return out


def export_snapshot(db_path: Path = DEFAULT_DB_PATH, data_root: Path = Path("data")) -> Path:
    """Compute performance rows and write a raw snapshot for the v2 parser."""
    rows = compute_rows(db_path)
    return save_raw_snapshot(
        root=data_root,
        source="kite",
        endpoint_name="performance",
        url="local:kite.sqlite",
        body=rows,
        fetched_at=datetime.now(timezone.utc).replace(microsecond=0),
        status_code=200,
    )
