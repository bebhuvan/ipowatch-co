"""Parse BSE ``MoreCompanyN`` listing performance feeds.

Catalog: ``docs/schema/raw_catalog/bse/ipo_performance_mainboard_<year>.json``.

The mainboard and SME variants share an identical row shape — we register
the same parser for both, plus the suffix-keyed concrete endpoints
``ipo_performance_(mainboard|sme)_<YYYY>`` discovered in raw snapshots.

Sample row::

    {
      "CompanyName": "Platinum Industries Limited",
      "Company_Short_Name": "PLATIND",
      "CurrentPrice": 223.6,
      "GainLoss": 52.6,
      "IMAGE": "https://www.bseindia.com/stock-share-price/.../544134/",
      "IssuePrice": 171.0,
      "ListedOn": "2024-03-05T00:00:00",
      "ListingDayClose": 220.9,
      "ListingDayGain": 49.9,
      "Time": "2024-12-31T00:00:00"
    }

This feed is the source of truth for ``listing_performance.*`` fields:
issue price (post-book), listing-day close, current price, and the two
gain figures (listing-day and overall). The `IMAGE` field is actually
the canonical BSE stock-detail page URL — useful for the link-out.
"""

from __future__ import annotations

import os
from typing import Any

from ...normalization import (
    UnitParseError,
    parse_indian_instant,
    parse_monetary_to_paise,
    sanitize_plaintext,
)
from ..identity import build_identity
from ..pipeline import Contribution, IssueJoinKey
from .registry import ParserContext, PARSERS, register_parser


@register_parser("bse", "ipo_performance_mainboard_<year>")
def parse_mainboard(body: Any, ctx: ParserContext) -> list[Contribution]:
    return _parse(body, ctx, board_type="Main Board")


@register_parser("bse", "ipo_performance_sme_<year>")
def parse_sme(body: Any, ctx: ParserContext) -> list[Contribution]:
    return _parse(body, ctx, board_type="SME Board")


def _parse(body: Any, ctx: ParserContext, *, board_type: str) -> list[Contribution]:
    rows = _extract_rows(body)
    out: list[Contribution] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        contribution = _row_to_contribution(row, ctx, board_type=board_type)
        if contribution is not None:
            out.append(contribution)
    return out


def _extract_rows(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, dict):
        rows = body.get("Table") or []
        if isinstance(rows, list):
            return rows
    if isinstance(body, list):
        return body
    return []


def _row_to_contribution(
    row: dict[str, Any],
    ctx: ParserContext,
    *,
    board_type: str,
) -> Contribution | None:
    company_name = sanitize_plaintext(row.get("CompanyName"))
    if not company_name:
        return None
    listed_on = _safe_instant(row.get("ListedOn"))
    if listed_on is None:
        return None

    identity = build_identity(
        company_name=company_name,
        listing_year=listed_on.year,
    )
    discriminator, _, value = identity.stable_join_key.partition(":")
    join_key = IssueJoinKey(discriminator=discriminator, value=value)

    issue_price_paise = _safe_money(row.get("IssuePrice"))
    listing_close_paise = _safe_money(row.get("ListingDayClose"))
    current_price_paise = _safe_money(row.get("CurrentPrice"))
    listing_gain_bps = _percent_to_bps(row.get("ListingDayGain"))
    overall_gain_bps = _percent_to_bps(row.get("GainLoss"))

    fields: dict[str, Any] = {
        "identity.company_name": company_name,
        "identity.slug": identity.slug,
        "identity.symbol": sanitize_plaintext(row.get("Company_Short_Name")),
        "identity.board_type": board_type,
        "identity.status": "Listed",
        "identity.issue_type": "IPO",
        "timeline.listing_date": listed_on.date().isoformat(),
        "pricing.issue_price_paise": issue_price_paise,
        "listing_performance.listing_close_price_paise": listing_close_paise,
        "listing_performance.current_price_paise": current_price_paise,
        "listing_performance.listing_gain_bps": listing_gain_bps,
        "listing_performance.current_gain_bps": overall_gain_bps,
        # IMAGE is the BSE stock detail URL despite the field name; surface
        # it as a "bse_stock_page" alias so the v2 page can link out.
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
    image = row.get("IMAGE")
    if isinstance(image, str) and image.startswith("http"):
        aliases.append(f"bse:stock_page:{image}")
    return aliases or None


def _safe_money(value: Any) -> int | None:
    if value is None or value == "" or value == 0:
        return None
    try:
        return parse_monetary_to_paise(value)
    except UnitParseError:
        return None


def _percent_to_bps(value: Any) -> int | None:
    """Convert a percent-like value (e.g., 49.9) to integer basis points.

    BSE reports gains as a number with implicit ``%`` units (e.g., 49.9
    means +49.9%). We round-half-even to integer bps (4990).
    """
    if value is None or value == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return int(round(n * 100))


def _safe_instant(value: Any):
    try:
        return parse_indian_instant(value)
    except UnitParseError:
        return None


# Concrete year-suffixed endpoints aren't predictable at import time —
# they're discovered when we walk raw snapshots. The pipeline's
# `collect_contributions` looks up parsers by exact (source, endpoint)
# pair, so we eagerly register the concrete year variants too by scanning
# data/raw/bse/ at module import.
def _register_year_variants() -> None:
    raw_root = "data/raw/bse"
    if not os.path.isdir(raw_root):
        return
    for entry in os.listdir(raw_root):
        if entry.startswith("ipo_performance_mainboard_"):
            year_suffix = entry.removeprefix("ipo_performance_mainboard_")
            if year_suffix.isdigit():
                key = ("bse", entry)
                if key not in PARSERS.by_key:
                    PARSERS.add("bse", entry, parse_mainboard)
        elif entry.startswith("ipo_performance_sme_"):
            year_suffix = entry.removeprefix("ipo_performance_sme_")
            if year_suffix.isdigit():
                key = ("bse", entry)
                if key not in PARSERS.by_key:
                    PARSERS.add("bse", entry, parse_sme)


_register_year_variants()
