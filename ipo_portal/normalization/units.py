"""Boundary parsers for v2 normalization.

Every helper accepts the kinds of messy values seen in NSE/BSE/PRIME/
Trendlyne/Moneycontrol snapshots and returns the canonical v2 storage
form. Helpers raise ``UnitParseError`` on input they cannot interpret;
the normalizer wraps that into a ``validate_v2`` finding rather than
swallowing it (see ``docs/data/EDGE_CASES.md`` and
``docs/data/FUTURE_PROOFING.md``).

Conventions enforced here
-------------------------
* Money → integer **paise** (₹ × 100). ``E.CUR.001``.
* Dates → ``datetime.date`` (date-only) or ``datetime`` with explicit
  ``Asia/Kolkata`` offset converted to UTC. ``E.DAT.001 / E.DAT.002``.
* Subscription multiples → ``Decimal`` (4-decimal precision).
  ``E.NUM.001``.
* Percentages → integer basis points (1% == 100 bps).
* Strings → NFC, BOM/CRLF stripped, surrounding whitespace trimmed.
  ``E.HTM.001 / E.HTM.003``.
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

try:  # Python 3.9+
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]


PAISE_PER_RUPEE = 100
BPS_PER_PERCENT = 100

IST = ZoneInfo("Asia/Kolkata")

# Plausible IPO-era year bounds for date sanity (E.DAT). Anything outside
# is treated as a source typo and rejected.
_MIN_PLAUSIBLE_YEAR = 1990


def _max_plausible_year() -> int:
    return datetime.now(timezone.utc).year + 2

# Sentinel values that mean "no data" across NSE/BSE/PRIME feeds.
_NULL_SENTINELS = frozenset(
    s.lower()
    for s in ("", "-", "--", "—", "na", "n/a", "null", "none", "nil", "?", ".")
)

# Indian-format numeric: allow commas in lakh/crore positions, optional sign
# and optional decimal fraction. Validates structure before we strip commas.
_NUMERIC_RE = re.compile(
    r"^[+-]?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?$"
)
# Currency text like "Rs. 1.5 Cr", "₹ 1,500 lakh", "INR 1,50,00,000".
_CURRENCY_TEXT_RE = re.compile(
    r"""^
    \s*
    (?:(?:Rs\.?|INR|₹)\s*)?
    (?P<value>[+-]?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?)
    \s*
    (?P<unit>cr|crore|crores|lakh|lakhs|lac|lacs|mn|million|k|thousand|paise)?
    \s*
    $""",
    re.IGNORECASE | re.VERBOSE,
)
# dd-MMM-yyyy / dd/MM/yyyy / dd-MM-yyyy / ISO date.
_DATE_PATTERNS = (
    re.compile(r"^(?P<d>\d{1,2})[-/\s](?P<m>[A-Za-z]{3,9})[-/\s](?P<y>\d{4})$"),
    re.compile(r"^(?P<d>\d{1,2})[-/](?P<m>\d{1,2})[-/](?P<y>\d{4})$"),
    re.compile(r"^(?P<y>\d{4})[-/](?P<m>\d{1,2})[-/](?P<d>\d{1,2})$"),
)
_MONTH_TO_NUM = {
    name: i + 1
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ]
    )
    for name in names
}
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_BOOL_TRUE = frozenset(("1", "true", "t", "y", "yes"))
_BOOL_FALSE = frozenset(("0", "false", "f", "n", "no"))


class UnitParseError(ValueError):
    """Raised when a value cannot be parsed into its canonical v2 form.

    The caller is expected to catch this and either record a
    ``validate_v2`` finding (warning/error/blocking depending on field)
    or treat the field as null with a documented ``null_meaning``.
    """


# ----------------------------------------------------------------- primitives


def nfc(value: str) -> str:
    """Return NFC-normalized string. ``E.HTM.002``."""
    return unicodedata.normalize("NFC", value)


def clean_text(value: Any) -> str | None:
    """Strip BOM, normalize line endings to LF, collapse whitespace.

    Returns ``None`` for null sentinels.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    text = value.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if text.lower() in _NULL_SENTINELS:
        return None
    return nfc(text)


def sanitize_plaintext(value: Any) -> str | None:
    """Strip HTML tags + decode entities; safe for display. ``E.HTM.001``."""
    text = clean_text(value)
    if text is None:
        return None
    stripped = _HTML_TAG_RE.sub("", text)
    return clean_text(html.unescape(stripped))


# BSE per-issue feeds prefix ``ScripName`` with a ``<n>+`` row marker, e.g.
# ``"1+ZEE MEDIA CORPORATION LIMITED"``. Strip only that exact artifact so
# legitimate digit-led names ("7NR Retail", "5paisa Capital") survive.
_ORDINAL_PREFIX_RE = re.compile(r"^\d+\+\s*")


def clean_company_name(value: Any) -> str | None:
    """Sanitize + remove the BSE ``<n>+`` ordinal prefix from a company name."""
    text = sanitize_plaintext(value)
    if text is None:
        return None
    return clean_text(_ORDINAL_PREFIX_RE.sub("", text))


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in _NULL_SENTINELS:
        return True
    return False


# --------------------------------------------------------------------- numbers


def parse_indian_number(value: Any) -> Decimal | None:
    """Parse "1,00,000" or "1.5" or 100000 into Decimal. ``E.NUM.002``.

    Returns ``None`` for null sentinels. Raises ``UnitParseError`` for
    structural malformation (e.g., letters mid-number).
    """
    if _is_null(value):
        return None
    if isinstance(value, bool):
        raise UnitParseError(f"boolean is not a number: {value!r}")
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    text = clean_text(value)
    if text is None:
        return None
    if not _NUMERIC_RE.match(text):
        raise UnitParseError(f"not a number: {text!r}")
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation as exc:  # pragma: no cover — guard
        raise UnitParseError(f"Decimal parse failed for {text!r}") from exc


def coerce_int(value: Any) -> int | None:
    """Integer coercion that accepts string-typed ints, commas, sentinels."""
    decimal_value = parse_indian_number(value)
    if decimal_value is None:
        return None
    if decimal_value % 1 != 0:
        raise UnitParseError(f"non-integer Decimal: {decimal_value}")
    return int(decimal_value)


def coerce_decimal(value: Any, places: int = 4) -> Decimal | None:
    """Decimal coercion with rounding to ``places`` after the point."""
    decimal_value = parse_indian_number(value)
    if decimal_value is None:
        return None
    quant = Decimal(10) ** -places
    return decimal_value.quantize(quant)


def coerce_bool(value: Any) -> bool | None:
    """Map ``"1"/"0"/"Y"/"N"/true/false`` to bool. Sentinels → None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = clean_text(value)
    if text is None:
        return None
    lowered = text.lower()
    if lowered in _BOOL_TRUE:
        return True
    if lowered in _BOOL_FALSE:
        return False
    raise UnitParseError(f"not a boolean: {value!r}")


# --------------------------------------------------------------------- money


def parse_monetary_to_paise(value: Any, default_unit: str | None = None) -> int | None:
    """Parse a monetary value into integer paise. ``E.CUR.001``.

    Accepts:
    * Raw numbers (interpreted via ``default_unit``, default ``"rupees"``).
    * Strings with explicit units: ``"₹ 1.5 Cr"``, ``"Rs. 1,500 lakhs"``,
      ``"INR 1500000"``.

    Returns ``None`` for null sentinels. Raises ``UnitParseError`` if the
    string can't be parsed or no unit can be inferred.

    The conversion table mirrors what NSE/BSE feeds actually use.
    """
    if _is_null(value):
        return None

    unit = (default_unit or "rupees").lower()
    if isinstance(value, str):
        text = clean_text(value)
        if text is None:
            return None
        match = _CURRENCY_TEXT_RE.match(text)
        if not match:
            raise UnitParseError(f"unparseable currency: {value!r}")
        raw_value = match.group("value")
        unit = (match.group("unit") or unit).lower()
        decimal_value = Decimal(raw_value.replace(",", ""))
    else:
        decimal_value = parse_indian_number(value)
        if decimal_value is None:
            return None

    rupees = _convert_to_rupees(decimal_value, unit)
    paise = (rupees * PAISE_PER_RUPEE).to_integral_value()
    return int(paise)


def _convert_to_rupees(value: Decimal, unit: str) -> Decimal:
    """Convert a numeric ``value`` in ``unit`` to rupees."""
    unit = unit.lower()
    if unit in ("rupees", "rupee", "rs", "rs.", "inr", "₹", ""):
        return value
    if unit == "paise":
        return value / Decimal(PAISE_PER_RUPEE)
    if unit in ("k", "thousand"):
        return value * Decimal(1_000)
    if unit in ("lakh", "lakhs", "lac", "lacs"):
        return value * Decimal(100_000)
    if unit in ("cr", "crore", "crores"):
        return value * Decimal(10_000_000)
    if unit in ("mn", "million"):
        return value * Decimal(1_000_000)
    raise UnitParseError(f"unknown currency unit: {unit!r}")


# --------------------------------------------------------------------- ratios


def parse_subscription_multiple(value: Any) -> Decimal | None:
    """Subscription "times" as Decimal(4 decimal places). ``E.NUM.001``.

    A trailing ``x`` is allowed (``"7.57x"``). Returns ``None`` for null.
    """
    if _is_null(value):
        return None
    if isinstance(value, str):
        text = clean_text(value)
        if text is None:
            return None
        text = text.rstrip("xX").strip()
        return coerce_decimal(text, places=4)
    return coerce_decimal(value, places=4)


def parse_percent_to_bps(value: Any) -> int | None:
    """Parse a percentage to integer basis points.

    Accepts ``"12.5%"``, ``"12.5"``, ``0.125`` (fraction). The
    distinction is made by the magnitude: values with a literal ``%``
    sign or > 1 are treated as percent; bare values ≤ 1 are treated as
    fractions. Caller should pass a clearly-formatted input where
    possible.
    """
    if _is_null(value):
        return None
    if isinstance(value, str):
        text = clean_text(value) or ""
        has_pct = text.endswith("%")
        if has_pct:
            text = text[:-1].strip()
        decimal_value = coerce_decimal(text, places=6)
        if decimal_value is None:
            return None
        if not has_pct and decimal_value.copy_abs() <= 1:
            decimal_value = decimal_value * Decimal(100)
    else:
        decimal_value = coerce_decimal(value, places=6)
        if decimal_value is None:
            return None
        if decimal_value.copy_abs() <= 1:
            decimal_value = decimal_value * Decimal(100)
    bps = (decimal_value * Decimal(BPS_PER_PERCENT)).to_integral_value()
    return int(bps)


# --------------------------------------------------------------------- dates


def parse_indian_date(value: Any, anchor_year: int | None = None) -> date | None:
    """Parse a date-only value from any seen format. ``E.DAT.001``.

    Accepts: ``"21-May-2026"``, ``"21/05/2026"``, ``"2026-05-21"``,
    ``date`` / ``datetime`` instances. Two-digit years raise
    ``UnitParseError`` (we never silently extrapolate).

    ``anchor_year`` enables **year repair**: when a date's parsed year is
    implausible (outside ``[1990, current+2]``) — almost always a source
    typo such as ``"0202-02-07"`` for ``"2022-02-07"`` — but the month
    and day are valid and a plausible ``anchor_year`` is supplied (e.g.,
    the issue's open-date year), the year is replaced with the anchor and
    the date is salvaged rather than discarded. Without an anchor, an
    implausible year still raises ``UnitParseError``.
    """
    if _is_null(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = clean_text(value)
    if text is None:
        return None
    for pattern in _DATE_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        day = int(match.group("d"))
        year_text = match.group("y")
        if len(year_text) != 4:
            raise UnitParseError(f"two-digit year not accepted: {text!r}")
        year = int(year_text)
        month_raw = match.group("m")
        if month_raw.isdigit():
            month = int(month_raw)
        else:
            month = _MONTH_TO_NUM.get(month_raw.lower())
            if month is None:
                raise UnitParseError(f"unknown month token: {month_raw!r}")
        if not 1 <= month <= 12 or not 1 <= day <= 31:
            raise UnitParseError(f"out-of-range date: {text!r}")
        # Year sanity (E.DAT): India's first modern IPO era is 1990s; a
        # year outside [1990, current+2] is almost always a source typo
        # (e.g., "0202" for "2022").
        if not _MIN_PLAUSIBLE_YEAR <= year <= _max_plausible_year():
            if anchor_year is not None and _MIN_PLAUSIBLE_YEAR <= anchor_year <= _max_plausible_year():
                # Repair: keep month/day, substitute the plausible anchor.
                year = anchor_year
            else:
                raise UnitParseError(
                    f"implausible year {year} in {text!r} "
                    f"(expected {_MIN_PLAUSIBLE_YEAR}..{_max_plausible_year()})"
                )
        return date(year, month, day)
    raise UnitParseError(f"unrecognized date format: {text!r}")


def parse_indian_instant(value: Any) -> datetime | None:
    """Parse a timestamp string assumed to be IST unless TZ-explicit.

    Output is a timezone-aware UTC datetime. ``E.DAT.002``.

    Accepts ISO 8601 (`"2026-05-21T09:30:00"` or with offset),
    `"21-May-2026 09:30:00"`, and `"21-May-2026 09:30"`. A bare time
    without offset is interpreted as ``Asia/Kolkata`` and converted.
    """
    if _is_null(value):
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=IST)
        return value.astimezone(timezone.utc)
    text = clean_text(value)
    if text is None:
        return None
    # Try ISO first.
    iso_text = text.replace(" ", "T")
    try:
        dt = datetime.fromisoformat(iso_text)
    except ValueError:
        dt = None
    if dt is None:
        # Try "dd-MMM-yyyy HH:MM[:SS]" form.
        date_part, _, time_part = text.partition(" ")
        parsed_date = parse_indian_date(date_part)
        if parsed_date is None:
            raise UnitParseError(f"unrecognized instant: {value!r}")
        hh, mm, ss = _parse_time_part(time_part)
        dt = datetime(parsed_date.year, parsed_date.month, parsed_date.day, hh, mm, ss)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(timezone.utc)


def _parse_time_part(text: str) -> tuple[int, int, int]:
    text = text.strip()
    if not text:
        return 0, 0, 0
    parts = text.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]), int(parts[1]), 0
        if len(parts) == 3:
            return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError as exc:
        raise UnitParseError(f"unrecognized time: {text!r}") from exc
    raise UnitParseError(f"unrecognized time: {text!r}")
