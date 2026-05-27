"""Parse BSE bid-book endpoints into the issue's final subscription summary.

We keep BOTH books (per the data owner):

* ``consolidated_bid_details_new_<id>`` / ``consolidated_bid_details_<id>``
  → ``subscription.consolidated`` (NSE+BSE combined).
* ``bid_details_<id>`` (``table2``) → ``subscription.by_exchange.bse``.

All three share the same row layout (``SRNo``, ``col2`` category,
``col3`` offered, ``col4`` bid, ``col5`` times). We reuse the v1
``trajectory`` SRNo→category mapper. This writes the **final** book onto
the issue record (the time-series still lives in ``trajectory_v2``).

Merges into the canonical record via the ``bse:ipo_no:<IPO_NO>`` alias.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

from ...normalization import clean_company_name
from ...trajectory import bse_category_key, is_header_row, is_total_row, parse_decimal, parse_int
from ..identity import build_identity
from ..pipeline import Contribution, IssueJoinKey
from .registry import PARSERS, ParserContext, register_parser


_IPO_NO_RE = re.compile(r"(?:consolidated_)?(?:bid_details)(?:_new)?_(\d+)$")
_CONSOLIDATED_PREFIXES = ("consolidated_bid_details_new_", "consolidated_bid_details_")


@register_parser("bse", "bid_details")
@register_parser("bse", "consolidated_bid_details")
@register_parser("bse", "consolidated_bid_details_new")
def parse(body: Any, ctx: ParserContext) -> list[Contribution]:
    rows = _rows(body)
    if not rows:
        return []
    company = None
    categories: list[dict[str, Any]] = []
    total_times: str | None = None
    for r in rows:
        srno = r.get("SRNo")
        label = r.get("col2")
        if company is None:
            company = clean_company_name(r.get("Scripname"))
        if is_header_row(srno, label):
            continue
        offered = parse_int(r.get("col3"))
        bid = parse_int(r.get("col4"))
        times = parse_decimal(r.get("col5"))
        if is_total_row(srno, label):
            total_times = _dec_str(times)
            continue
        key = bse_category_key(str(srno or ""))
        if key is None:
            continue
        categories.append(
            {
                "category": key,
                "shares_offered": offered,
                "shares_bid": bid,
                "times_x": _dec_str(times),
            }
        )

    if not company or not categories:
        return []

    ipo_no = _ipo_no(ctx.endpoint)
    identity = build_identity(company_name=company, listing_year=_fetch_year(ctx.snapshot_at))
    # Key by IPO_NO (unique per issue) so different issues of one company
    # don't collapse via a shared fetch-year key.
    if ipo_no:
        join_key = IssueJoinKey(discriminator="bse_ipo_no", value=ipo_no)
    else:
        discriminator, _, value = identity.stable_join_key.partition(":")
        join_key = IssueJoinKey(discriminator=discriminator, value=value)

    is_consolidated = ctx.endpoint.startswith(_CONSOLIDATED_PREFIXES)
    book = {"categories": categories, "total_times_x": total_times}

    fields: dict[str, Any] = {
        "identity.company_name": company,
        "identity.slug": identity.slug,
        "identity.issue_type": "IPO",
    }
    if is_consolidated:
        fields["subscription.consolidated"] = book
        if total_times:
            fields["subscription.overall_times_x"] = total_times
    else:
        # Dotted leaf path so NSE + BSE per-exchange books coexist.
        fields["subscription.by_exchange.bse"] = book
    if ipo_no:
        fields["identity.aliases"] = [f"bse:ipo_no:{ipo_no}"]

    return [
        Contribution(
            source=ctx.source,
            endpoint=ctx.endpoint,
            snapshot_at=ctx.snapshot_at,
            join_key=join_key,
            fields=fields,
        )
    ]


def _rows(body: Any) -> list[dict[str, Any]]:
    if not isinstance(body, dict):
        return []
    for key in ("table1", "table2"):
        rows = body.get(key)
        if isinstance(rows, list) and rows:
            return rows
    return []


def _dec_str(value: Any) -> str | None:
    if value is None:
        return None
    return f"{float(value):.4f}"


def _ipo_no(endpoint: str) -> str | None:
    m = _IPO_NO_RE.search(endpoint)
    return m.group(1) if m else None


def _fetch_year(snapshot_at: str) -> int:
    try:
        return datetime.fromisoformat(snapshot_at.replace("Z", "+00:00")).year
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc).year


def _register_concrete() -> None:
    raw_root = "data/raw/bse"
    if not os.path.isdir(raw_root):
        return
    for entry in os.listdir(raw_root):
        if not _IPO_NO_RE.search(entry):
            continue
        if entry.startswith(_CONSOLIDATED_PREFIXES) or entry.startswith("bid_details_"):
            key = ("bse", entry)
            if key not in PARSERS.by_key:
                PARSERS.add("bse", entry, parse)


_register_concrete()
