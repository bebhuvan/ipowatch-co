"""Parse the Kite-derived performance snapshot into v2 contributions.

Source: ``data/raw/kite/performance/<ts>.json`` written by
``ipo_portal.kite_v2.export_snapshot``. Each row carries the issue's
company name, listing date, the exchange Kite actually priced it on, and
the computed listing-gain / current-performance basis points.

SME single-exchange handling
-----------------------------
Many SME issues list on **only** NSE Emerge *or* BSE SME, never both. We
therefore:

* join to the canonical issue by ``company_name + listing_year`` (NOT by
  symbol), so a one-exchange SME matches regardless of which exchange
  symbol the other sources happened to record;
* attach the symbol under an exchange-qualified alias
  (``kite:nse:<sym>`` / ``kite:bse:<sym>``) so the v2 record records
  which venue the price came from without implying a dual listing;
* set ``board_type`` only when the symbol suffix makes it unambiguous
  (``-SM``/``-ST`` = NSE SME; we don't guess otherwise).

Kite is ``enrichment`` tier — it wins ``current_price`` per
``SOURCE_PRECEDENCE.yaml`` but defers to exchange feeds on issue price.
"""

from __future__ import annotations

from typing import Any

from ..identity import build_identity
from ..pipeline import Contribution, IssueJoinKey
from ...normalization import UnitParseError, parse_indian_date, sanitize_plaintext
from .registry import ParserContext, register_parser


# NSE SME trading-symbol suffixes. BSE SME has no symbol suffix (scrip-code
# based), so we never infer "BSE SME" from a symbol alone.
_NSE_SME_SUFFIXES = ("-SM", "-ST")


@register_parser("kite", "performance")
def parse(body: Any, ctx: ParserContext) -> list[Contribution]:
    if not isinstance(body, list):
        return []
    out: list[Contribution] = []
    for row in body:
        if not isinstance(row, dict):
            continue
        contribution = _row_to_contribution(row, ctx)
        if contribution is not None:
            out.append(contribution)
    return out


def _row_to_contribution(row: dict[str, Any], ctx: ParserContext) -> Contribution | None:
    company = sanitize_plaintext(row.get("company_name"))
    if not company:
        return None
    listing_date = _safe_date(row.get("listing_date"))
    if listing_date is None:
        return None

    identity = build_identity(company_name=company, listing_year=listing_date.year)
    discriminator, _, value = identity.stable_join_key.partition(":")
    join_key = IssueJoinKey(discriminator=discriminator, value=value)

    symbol = sanitize_plaintext(row.get("symbol"))
    kite_exchange = (row.get("kite_exchange") or "").upper()

    fields: dict[str, Any] = {
        "identity.company_name": company,
        "identity.slug": identity.slug,
        "identity.issue_type": "IPO",
        "timeline.listing_date": listing_date.isoformat(),
        # Listing-day + current performance (the gain math).
        "listing_performance.listing_open_price_paise": row.get("listing_open_paise"),
        "listing_performance.listing_close_price_paise": row.get("listing_close_paise"),
        "listing_performance.current_price_paise": row.get("current_price_paise"),
        "listing_performance.listing_gain_bps": row.get("listing_gain_bps"),
        # current_gain_bps is the headline "where is it now vs issue price".
        "listing_performance.current_gain_bps": row.get("current_gain_from_issue_bps"),
        "pricing.issue_price_paise": row.get("issue_price_paise"),
    }

    board_type = _board_type(symbol)
    if board_type:
        fields["identity.board_type"] = board_type

    aliases = _aliases(symbol, kite_exchange)
    if aliases:
        fields["identity.aliases"] = aliases

    fields = {k: v for k, v in fields.items() if v is not None}

    return Contribution(
        source=ctx.source,
        endpoint=ctx.endpoint,
        snapshot_at=ctx.snapshot_at,
        join_key=join_key,
        fields=fields,
    )


def _board_type(symbol: str | None) -> str | None:
    if not symbol:
        return None
    upper = symbol.upper()
    if any(upper.endswith(sfx) for sfx in _NSE_SME_SUFFIXES):
        return "SME Board"
    return None  # never guess BSE SME / Main Board from a symbol alone


def _aliases(symbol: str | None, kite_exchange: str) -> list[str] | None:
    if not symbol or not kite_exchange:
        return None
    venue = kite_exchange.lower()
    return [f"kite:{venue}:{symbol}"]


def _safe_date(value: Any):
    try:
        return parse_indian_date(value)
    except UnitParseError:
        return None
