"""Parse Moneycontrol ``listed-ipo`` paginated feed.

Catalog: ``docs/schema/raw_catalog/moneycontrol/listed_ipos_page_<n>.json``.

Row shape::

    {
      "company_code": 14620279,
      "company_name": "RFBL Flexi Pack ",   // trailing whitespace common
      "sc_id": "RFP01",
      "ipo_type": "SME" | "Main Board",
      "issue_price": 50,                     // ₹ per share
      "issue_size": 353250000,               // ₹ total (raw rupees)
      "listing_date": "2026-05-19",
      "listing_gain": 5,                     // % vs issue price
      "todays_gain": 21.33,                  // % today
      "last_price": "63.70",                 // ₹ live
      "dt_open": 52.5,
      "dt_close": 55.1,
      "total_subs": 20.45,                   // overall subscription x
      "url": "packaging-packaging-materials/rfblflexipack/RFP01"
    }

Moneycontrol is `enrichment`-tier per SOURCE_PRECEDENCE.yaml — it loses
to NSE/BSE on issue_price, issue_size, etc., but wins on `current_price`
when Kite isn't available. We populate every field; the precedence
resolver picks the winner during merge.

The endpoint name is family-collapsed in the registry; we register the
parser for the family key and walk concrete pages from disk at import.
"""

from __future__ import annotations

import os
from typing import Any

from ...normalization import (
    UnitParseError,
    coerce_decimal,
    parse_indian_date,
    parse_monetary_to_paise,
    sanitize_plaintext,
)
from ..identity import build_identity
from ..pipeline import Contribution, IssueJoinKey
from .registry import PARSERS, ParserContext, register_parser


@register_parser("moneycontrol", "listed_ipos_page_<n>")
def parse(body: Any, ctx: ParserContext) -> list[Contribution]:
    rows = _extract_rows(body)
    out: list[Contribution] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        contribution = _row_to_contribution(row, ctx)
        if contribution is not None:
            out.append(contribution)
    return out


def _extract_rows(body: Any) -> list[dict[str, Any]]:
    if not isinstance(body, dict):
        return []
    data = body.get("data") or {}
    listed = data.get("listedIpo") if isinstance(data, dict) else None
    return listed if isinstance(listed, list) else []


def _row_to_contribution(row: dict[str, Any], ctx: ParserContext) -> Contribution | None:
    company_name = sanitize_plaintext(row.get("company_name"))
    if not company_name:
        return None
    listing_date = _safe_date(row.get("listing_date"))
    if listing_date is None:
        return None

    identity = build_identity(
        company_name=company_name,
        listing_year=listing_date.year,
    )
    discriminator, _, value = identity.stable_join_key.partition(":")
    join_key = IssueJoinKey(discriminator=discriminator, value=value)

    ipo_type = (row.get("ipo_type") or "").strip().upper()
    board_type = "SME Board" if ipo_type == "SME" else "Main Board" if ipo_type else None

    issue_price_paise = _safe_money(row.get("issue_price"))
    issue_size_paise = _safe_money(row.get("issue_size"))
    last_price_paise = _safe_money(row.get("last_price"))
    listing_gain_bps = _pct_to_bps(row.get("listing_gain"))
    todays_gain_bps = _pct_to_bps(row.get("todays_gain"))
    total_subs_x = _decimal_str(row.get("total_subs"))

    fields: dict[str, Any] = {
        "identity.company_name": company_name,
        "identity.slug": identity.slug,
        "identity.symbol": sanitize_plaintext(row.get("sc_id")),
        "identity.board_type": board_type,
        "identity.status": "Listed",
        "identity.issue_type": "IPO",
        "timeline.listing_date": listing_date.isoformat(),
        "pricing.issue_price_paise": issue_price_paise,
        "pricing.issue_size_paise": issue_size_paise,
        "listing_performance.current_price_paise": last_price_paise,
        "listing_performance.listing_gain_bps": listing_gain_bps,
        "listing_performance.current_gain_bps": todays_gain_bps,
        "subscription.overall_times_x": total_subs_x,
        "identity.aliases": _aliases(row),
    }
    fields = {k: v for k, v in fields.items() if v is not None}

    return Contribution(
        source=ctx.source,
        endpoint=ctx.endpoint,
        snapshot_at=ctx.snapshot_at,
        join_key=join_key,
        fields=fields,
    )


def _aliases(row: dict[str, Any]) -> list[str] | None:
    aliases: list[str] = []
    url = row.get("url")
    if isinstance(url, str) and url:
        aliases.append(f"moneycontrol:slug:{url}")
    code = row.get("company_code")
    if code:
        aliases.append(f"moneycontrol:company_code:{code}")
    return aliases or None


def _safe_money(value: Any) -> int | None:
    if value is None or value == "" or value == 0:
        return None
    try:
        return parse_monetary_to_paise(value)
    except UnitParseError:
        return None


def _safe_date(value: Any):
    try:
        return parse_indian_date(value)
    except UnitParseError:
        return None


def _decimal_str(value: Any) -> str | None:
    dec = None
    try:
        dec = coerce_decimal(value, places=4)
    except UnitParseError:
        return None
    return None if dec is None else str(dec)


def _pct_to_bps(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return int(round(n * 100))


def _register_concrete_pages() -> None:
    """Register every concrete ``listed_ipos_page_<n>`` snapshot directory."""
    raw_root = "data/raw/moneycontrol"
    if not os.path.isdir(raw_root):
        return
    for entry in os.listdir(raw_root):
        if not entry.startswith("listed_ipos_page_"):
            continue
        suffix = entry.removeprefix("listed_ipos_page_")
        if not suffix.isdigit():
            continue
        key = ("moneycontrol", entry)
        if key not in PARSERS.by_key:
            PARSERS.add("moneycontrol", entry, parse)


_register_concrete_pages()
