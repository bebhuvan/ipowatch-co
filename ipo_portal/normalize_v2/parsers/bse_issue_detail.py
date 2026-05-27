"""Parse BSE ``GetMkt_ISSUE_BBS_IPO`` per-issue detail.

Catalog: ``docs/schema/raw_catalog/bse/issue_detail_<id>.json`` (68 fields).
Body shape (keys are ``IPONO_<n>``):

* ``IPONO_0`` — list[1], the master row: BRLM, co-BRLM, registrar,
  sponsor bank, syndicate, market lot, min bid qty, tick size, face
  value, price band, issue size, anchor details, symbol, scrip code.
  Names are ``^``-delimited: ``"NAME^address^email^..."``.
* ``IPONO_1`` — dynamic columns (day-by-day subscription etc.).
* ``IPONO_2`` / ``IPONO_3`` — demand schedule rows (Price, Quantity).
* ``IPONO_4`` — public notices.

This feed has no listing date, so we merge into the canonical record via
the ``bse:ipo_no:<IPO_NO>`` alias (the consolidation step unions on it).
The standalone join key uses the fetch year only as a fallback.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ...normalization import (
    UnitParseError,
    clean_company_name,
    coerce_int,
    parse_monetary_to_paise,
    sanitize_plaintext,
)
import os

from ..identity import build_identity
from ..pipeline import Contribution, IssueJoinKey
from .registry import PARSERS, ParserContext, register_parser


_IPO_NO_RE = re.compile(r"issue_detail_(\d+)$")


@register_parser("bse", "issue_detail")
def parse(body: Any, ctx: ParserContext) -> list[Contribution]:
    if not isinstance(body, dict):
        return []
    master_rows = body.get("IPONO_0")
    if not isinstance(master_rows, list) or not master_rows:
        return []
    master = master_rows[0]
    if not isinstance(master, dict):
        return []

    company = clean_company_name(master.get("ScripName"))
    if not company:
        return []

    ipo_no = str(master.get("IPO_NO") or _ipo_no_from_endpoint(ctx.endpoint) or "").strip()

    # Key by the BSE IPO_NO — it's unique per issue, so distinct issues of
    # the same company stay distinct. (Keying by name+fetch-year would
    # collapse every IPO_NO of one company into a single bogus record and
    # fuse their prices.) Falls back to name+fetch-year only if no IPO_NO.
    identity = build_identity(company_name=company, listing_year=_fetch_year(ctx.snapshot_at))
    if ipo_no:
        join_key = IssueJoinKey(discriminator="bse_ipo_no", value=ipo_no)
    else:
        discriminator, _, value = identity.stable_join_key.partition(":")
        join_key = IssueJoinKey(discriminator=discriminator, value=value)

    fields: dict[str, Any] = {
        "identity.company_name": company,
        "identity.slug": identity.slug,
        # Derive type from Security_Type — the IPO_NO sequence spans every
        # issuance type (equity / debt / OFS / REIT / InvIT), so hardcoding
        # IPO would mislabel thousands of backfilled debt/OFS rows.
        "identity.issue_type": _issue_type(master),
        # Pricing / mechanics.
        "pricing.market_lot": _int(master.get("Market_Lot")),
        "pricing.min_bid_qty": _int(master.get("Minimum_Bid_Quantity")),
        "pricing.tick_size_paise": _money(master.get("Tick_Size")),
        "pricing.face_value_paise": _money(master.get("Face_Value")),
        # Parties (dealmakers).
        "parties.lead_managers": _names(master.get("Book_Running_Lead_Manager")),
        "parties.co_lead_managers": _names(master.get("Co_Book_Running_Lead_Manager")),
        "parties.registrar": _first_name(master.get("Registrar")),
        "parties.sponsor_bank": _first_name(master.get("Sponsor_Bank")),
        "parties.syndicate_members": _names(master.get("Syndicate_Member")),
        # Demand schedule (the book).
        "book_building.demand_schedule": _demand_schedule(body),
        "book_building.daily_subscription": _daily(body),
    }

    # Anchor presence (the detail blob; full investor list comes from the RHP).
    anchor = sanitize_plaintext(master.get("Anchor_Details"))
    aliases: list[str] = []
    if ipo_no:
        aliases.append(f"bse:ipo_no:{ipo_no}")
    scrip = master.get("ScripCode") or master.get("Scrip_cd")
    if scrip:
        aliases.append(f"bse:scrip_code:{scrip}")
    if aliases:
        fields["identity.aliases"] = aliases

    # Price band like "91" or "91-95".
    band = _band(master.get("Price_Band"))
    if band[0] is not None:
        fields["pricing.price_band_lower_paise"] = band[0]
    if band[1] is not None:
        fields["pricing.price_band_upper_paise"] = band[1]

    issue_size_shares = _int(master.get("Issue_Size_No_of_shares"))
    if issue_size_shares:
        fields["pricing.issue_size_shares"] = issue_size_shares

    fields = {k: v for k, v in fields.items() if v not in (None, [], {})}
    return [
        Contribution(
            source=ctx.source,
            endpoint=ctx.endpoint,
            snapshot_at=ctx.snapshot_at,
            join_key=join_key,
            fields=fields,
        )
    ]


def _issue_type(master: dict[str, Any] | Any) -> str:
    """Map BSE ``Security_Type`` free-text to the canonical issue_type enum.

    Examples seen: "Equity", "OFS", "Secured Redeemable non-convertible
    Debentures", "Tax Free Bonds…", "Units of REITS", "Units of the
    InvIT", "IDR". We do NOT default unknowns to IPO (that was the bug).
    """
    if isinstance(master, dict):
        security_type = master.get("Security_Type")
        context = " ".join(
            sanitize_plaintext(master.get(k)) or ""
            for k in (
                "Security_Type",
                "Issue_Period",
                "Remarks",
                "Notes",
                "Prospectus_GID",
                "Price_Band_Advertisement",
            )
        ).lower()
    else:
        security_type = master
        context = sanitize_plaintext(master) or ""
        context = context.lower()
    if "fpo" in context:
        return "FPO"
    if "rights" in context or "right issue" in context:
        return "Rights"
    if "buyback" in context:
        return "Buyback"
    s = (sanitize_plaintext(security_type) or "").lower()
    if not s:
        return "Others"
    if "ofs" in s:
        return "OFS"
    if "debenture" in s or "ncd" in s or "bond" in s:
        return "NCD"
    if "reit" in s:
        return "REIT"
    if "invit" in s:
        return "InvIT"
    if "idr" in s:
        return "Others"
    if "equity" in s:
        return "IPO"
    return "Others"


def _names(value: Any) -> list[str] | None:
    """Extract intermediary firm name(s) from a BSE party blob.

    Within one firm the fields are delimited by ``^`` and ``|``
    (NAME^address|email|contact). Multiple firms are separated by ``;``
    or a newline. So: split firms on ``;``/newline, then take the part
    before the first ``^`` or ``|`` as the firm name (drop address/
    email/contact)."""
    text = sanitize_plaintext(value)
    if not text:
        return None
    names: list[str] = []
    for firm in re.split(r"[;\n]+", text):
        name = re.split(r"[\^|]", firm, maxsplit=1)[0].strip()
        # Guard against obvious non-names (emails) leaking through.
        if name and "@" not in name:
            names.append(name)
    return names or None


def _first_name(value: Any) -> str | None:
    names = _names(value)
    return names[0] if names else None


def _demand_schedule(body: dict[str, Any]) -> list[dict[str, Any]] | None:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[int | None, int | None]] = set()
    for key in ("IPONO_2", "IPONO_3"):
        block = body.get(key)
        if not isinstance(block, list):
            continue
        for r in block:
            if not isinstance(r, dict):
                continue
            price_paise = _money(r.get("Price"))
            qty = _int(r.get("Quantity"))
            sig = (price_paise, qty)
            if sig in seen or (price_paise is None and qty is None):
                continue
            seen.add(sig)
            rows.append({"price_paise": price_paise, "quantity": qty, "cumulative_quantity": None})
    return rows or None


def _daily(body: dict[str, Any]) -> list[dict[str, Any]] | None:
    block = body.get("IPONO_1")
    if not isinstance(block, list):
        return None
    out: list[dict[str, Any]] = []
    for r in block:
        if not isinstance(r, dict):
            continue
        name = sanitize_plaintext(r.get("DYN_COLNAME"))
        val = sanitize_plaintext(r.get("DYN_COLValue"))
        if name and val and name.lower().startswith("day"):
            m = re.search(r"\d+", name)
            out.append({"day": int(m.group()) if m else None, "times_x": val})
    return out or None


def _band(value: Any) -> tuple[int | None, int | None]:
    text = sanitize_plaintext(value)
    if not text:
        return None, None
    parts = re.split(r"\s*[-–]\s*", text)
    if len(parts) == 1:
        p = _money(parts[0])
        return p, p
    return _money(parts[0]), _money(parts[1])


def _int(value: Any) -> int | None:
    try:
        return coerce_int(value)
    except UnitParseError:
        return None


def _money(value: Any) -> int | None:
    if value in (None, "", "0", 0):
        return None
    try:
        paise = parse_monetary_to_paise(value)
    except UnitParseError:
        return None
    # BSE publishes "0.00" as the lower band for fixed-price issues — a
    # zero price is never real, so treat it as absent.
    return paise or None


def _ipo_no_from_endpoint(endpoint: str) -> str | None:
    m = _IPO_NO_RE.search(endpoint)
    return m.group(1) if m else None


def _fetch_year(snapshot_at: str) -> int:
    try:
        return datetime.fromisoformat(snapshot_at.replace("Z", "+00:00")).year
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc).year


def _register_concrete() -> None:
    """Register every concrete ``issue_detail_<IPO_NO>`` snapshot dir."""
    raw_root = "data/raw/bse"
    if not os.path.isdir(raw_root):
        return
    for entry in os.listdir(raw_root):
        if _IPO_NO_RE.search(entry) and entry.startswith("issue_detail_"):
            key = ("bse", entry)
            if key not in PARSERS.by_key:
                PARSERS.add("bse", entry, parse)


_register_concrete()
