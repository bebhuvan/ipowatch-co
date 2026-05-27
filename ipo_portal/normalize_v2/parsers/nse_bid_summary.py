"""Parse NSE per-issue feeds (subscription book + issue detail).

NSE per-issue feeds are fetched only for *active* issues (nested
discovery from ``ipo_current_issue``) and are **symbol-keyed** — they
carry no IPO_NO or clean company name. So we emit a contribution keyed
by ``symbol`` and let the consolidation step union it into the canonical
record that has the matching ``identity.symbol``.

Sources:
* ``consolidated_bid_details_<symbol>`` (NSE ``ipo-active-category``) →
  ``dataList`` of category / offered / bid / times.
* ``issue_detail_<symbol>_<series>`` → ``activeCat.dataList`` (the book,
  same shape) PLUS ``issueInfo.dataList`` — a title/value table with the
  rich issue detail (price band, lot, face/tick size, lead managers,
  sponsor bank, registrar, issue period, RHP URL, full company name).
  This is the NSE-side equivalent of BSE ``issue_detail``.

Keeps the NSE book alongside the BSE book (we keep BOTH exchanges'
books, just like we keep consolidated + per-exchange on BSE). Reuses the
v1 ``nse_category_key`` mapper.
"""

from __future__ import annotations

import os
import re
from typing import Any

from ...normalization import (
    UnitParseError,
    parse_indian_date,
    parse_monetary_to_paise,
    sanitize_plaintext,
)
from ...trajectory import bse_category_key, nse_category_key, parse_decimal, parse_int
from ..pipeline import Contribution, IssueJoinKey
from .registry import PARSERS, ParserContext, register_parser


# issue_detail_<symbol>_<series> / consolidated_bid_details_<symbol> /
# bid_details_<symbol>_<series>
_DETAIL_RE = re.compile(r"^(?:issue_detail|bid_details)_([a-z0-9]+)_[a-z0-9]+$", re.IGNORECASE)
_CONS_RE = re.compile(r"^consolidated_bid_details_([a-z0-9]+)$", re.IGNORECASE)


@register_parser("nse", "consolidated_bid_details")
@register_parser("nse", "issue_detail")
@register_parser("nse", "bid_details")
def parse(body: Any, ctx: ParserContext) -> list[Contribution]:
    symbol = (_symbol_hint(body) or _symbol_from_endpoint(ctx.endpoint) or "").upper()
    if not symbol:
        return []

    # The subscription summary (offered/bid/times) lives in dataList /
    # activeCat.dataList; the per-category bid book (shares_bid +
    # applications, SRNo-coded like BSE) lives in bidDetails / data.
    cats: dict[str, dict[str, Any]] = {}
    total_times: str | None = None

    for r in _summary_rows(body):
        label = str(r.get("category") or "").strip()
        if not label or label.lower() == "category":
            continue
        if label.lower().startswith("total"):
            t = parse_decimal(r.get("noOfTotalMeant"))
            if t:
                total_times = _dec(t)
            continue
        key = bse_category_key(str(r.get("srNo") or "")) or nse_category_key(label)
        if key is None:
            continue
        c = cats.setdefault(key, {"category": key})
        c["shares_offered"] = parse_int(r.get("noOfShareOffered") or r.get("noOfShareO"))
        c["shares_bid"] = parse_int(r.get("noOfSharesBid"))
        c["times_x"] = _dec(parse_decimal(r.get("noOfTotalMeant")))

    for r in _bid_rows(body):
        key = bse_category_key(str(r.get("srNo") or ""))
        if key is None:
            continue
        c = cats.setdefault(key, {"category": key})
        bid = parse_int(r.get("noOfshareBid"))
        if bid is not None:
            c.setdefault("shares_bid", bid)
        apps = parse_int(r.get("noofapplication"))
        if apps is not None:
            c["applications"] = apps

    categories = list(cats.values())
    detail = _issue_info_fields(body)
    if not categories and not total_times and not detail:
        return []

    join_key = IssueJoinKey(discriminator="symbol", value=symbol)
    fields: dict[str, Any] = {
        "identity.symbol": symbol,
        "identity.aliases": [f"nse:symbol:{symbol}"],
    }
    if categories or total_times:
        # Dotted leaf path so the NSE and BSE per-exchange books coexist
        # (the merge resolves per-path; a whole-dict value would clobber).
        fields["subscription.by_exchange.nse"] = {
            "categories": categories,
            "total_times_x": total_times,
        }
    fields.update(detail)
    return [
        Contribution(
            source=ctx.source,
            endpoint=ctx.endpoint,
            snapshot_at=ctx.snapshot_at,
            join_key=join_key,
            fields=fields,
        )
    ]


def _summary_rows(body: Any) -> list[dict[str, Any]]:
    """Subscription summary rows (offered/bid/times)."""
    if not isinstance(body, dict):
        return []
    if isinstance(body.get("dataList"), list):
        return body["dataList"]
    active = body.get("activeCat")
    if isinstance(active, dict) and isinstance(active.get("dataList"), list):
        return active["dataList"]
    return []


def _bid_rows(body: Any) -> list[dict[str, Any]]:
    """Per-category bid book rows (shares_bid + applications, SRNo-coded)."""
    if not isinstance(body, dict):
        return []
    if isinstance(body.get("bidDetails"), list):
        return body["bidDetails"]
    if isinstance(body.get("data"), list):
        return body["data"]
    return []


_NUM_RE = re.compile(r"\d[\d,]*\.?\d*")


def _issue_info_fields(body: Any) -> dict[str, Any]:
    """Extract the rich issue detail from ``issueInfo.dataList`` (a title/
    value table). Present only on the ``issue_detail`` endpoint; the bid-book
    endpoints have no ``issueInfo``, so this returns ``{}`` for them.
    """
    info = body.get("issueInfo") if isinstance(body, dict) else None
    if not isinstance(info, dict):
        return {}
    kv: dict[str, Any] = {}
    for row in info.get("dataList") or []:
        if isinstance(row, dict):
            title = (row.get("title") or "").strip()
            if title and row.get("value") is not None:
                kv[title] = row["value"]

    out: dict[str, Any] = {}
    name = sanitize_plaintext(info.get("heading"))
    if name:
        out["identity.company_name"] = name

    lo, hi = _price_range(kv.get("Price Range"))
    if lo is not None:
        out["pricing.price_band_lower_paise"] = lo
    if hi is not None:
        out["pricing.price_band_upper_paise"] = hi
    for fld, label in (("face_value_paise", "Face Value"), ("tick_size_paise", "Tick Size")):
        paise = _rupee_paise(kv.get(label))
        if paise:
            out[f"pricing.{fld}"] = paise
    lot = _lead_int(kv.get("Lot Size"))
    if lot:
        out["pricing.market_lot"] = lot

    open_date, close_date = _issue_period(kv.get("Issue Period"))
    if open_date:
        out["timeline.open_date"] = open_date
    if close_date:
        out["timeline.close_date"] = close_date

    lead = _firms(kv.get("Book Running Lead Managers"))
    if lead:
        out["parties.lead_managers"] = lead
    sponsor = _firms(kv.get("Sponsor Bank"))
    if sponsor:
        out["parties.sponsor_bank"] = sponsor[0]
    registrar = _firms(kv.get("Name of the Registrar"))
    if registrar:
        out["parties.registrar"] = registrar[0]

    rhp = _http_url(kv.get("Red Herring Prospectus"))
    if rhp:
        out["documents.rhp_url"] = rhp
    return out


def _price_range(value: Any) -> tuple[int | None, int | None]:
    """``"Rs.132 to Rs.139 per equity share"`` → (13200, 13900) paise."""
    text = sanitize_plaintext(value)
    if not text:
        return None, None
    paise: list[int] = []
    for n in _NUM_RE.findall(text)[:2]:
        try:
            paise.append(parse_monetary_to_paise(n))
        except UnitParseError:
            continue
    if not paise:
        return None, None
    if len(paise) == 1:
        return paise[0], paise[0]
    return paise[0], paise[1]


def _rupee_paise(value: Any) -> int | None:
    """``"Rs.10"`` → 1000 paise; ``"Re.1"`` → 100 paise."""
    text = sanitize_plaintext(value)
    if not text:
        return None
    m = _NUM_RE.search(text)
    if not m:
        return None
    try:
        return parse_monetary_to_paise(m.group(0)) or None
    except UnitParseError:
        return None


def _lead_int(value: Any) -> int | None:
    """``"1000 Equity Shares"`` → 1000."""
    text = sanitize_plaintext(value)
    if not text:
        return None
    m = _NUM_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", "").split(".")[0])
    except ValueError:
        return None


def _issue_period(value: Any) -> tuple[str | None, str | None]:
    """``"21-May-2026 to 25-May-2026"`` → ("2026-05-21", "2026-05-25")."""
    text = sanitize_plaintext(value)
    if not text:
        return None, None
    parts = re.split(r"\s+to\s+", text, maxsplit=1)
    return _iso(parts[0]), (_iso(parts[1]) if len(parts) > 1 else None)


def _iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        d = parse_indian_date(value.strip())
    except UnitParseError:
        return None
    return d.isoformat() if d else None


def _firms(value: Any) -> list[str] | None:
    """Split a firm list on ``", "`` and ``" and "`` ("X and Y" → [X, Y])."""
    text = sanitize_plaintext(value)
    if not text:
        return None
    firms = [p.strip() for p in re.split(r"\s+and\s+|,\s*", text) if p.strip()]
    return firms or None


def _http_url(value: Any) -> str | None:
    text = sanitize_plaintext(value)
    if text and text.lower().startswith("http"):
        return text
    return None


def _symbol_hint(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    # consolidated has "symbol"; issue_detail has "companyName" = the symbol.
    return body.get("symbol") or body.get("companyName")


def _dec(value: Any) -> str | None:
    return None if value is None else f"{float(value):.4f}"


def _symbol_from_endpoint(endpoint: str) -> str | None:
    m = _DETAIL_RE.match(endpoint) or _CONS_RE.match(endpoint)
    return m.group(1) if m else None


def _register_concrete() -> None:
    raw_root = "data/raw/nse"
    if not os.path.isdir(raw_root):
        return
    for entry in os.listdir(raw_root):
        if _DETAIL_RE.match(entry) or _CONS_RE.match(entry):
            key = ("nse", entry)
            if key not in PARSERS.by_key:
                PARSERS.add("nse", entry, parse)


_register_concrete()
