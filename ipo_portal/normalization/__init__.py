"""V2 normalization helpers.

These primitives enforce the storage conventions documented in
``docs/data/FUTURE_PROOFING.md`` section 8:

* Monetary values stored as integer paise (``₹ × 100``).
* Dates stored as ISO 8601 (date-only or instant-with-offset).
* Subscription multiples stored as ``Decimal``.
* Percentages stored as integer basis points.
* All strings NFC-normalized, BOM/CRLF cleaned.

Each helper is a single-purpose function used by ``normalize_v2.py`` at
the boundary where raw upstream values become canonical v2 fields.
"""

from __future__ import annotations

from .units import (
    PAISE_PER_RUPEE,
    BPS_PER_PERCENT,
    parse_indian_date,
    parse_indian_instant,
    parse_indian_number,
    parse_monetary_to_paise,
    parse_subscription_multiple,
    parse_percent_to_bps,
    coerce_int,
    coerce_decimal,
    coerce_bool,
    clean_text,
    sanitize_plaintext,
    clean_company_name,
    nfc,
    UnitParseError,
)

__all__ = [
    "PAISE_PER_RUPEE",
    "BPS_PER_PERCENT",
    "parse_indian_date",
    "parse_indian_instant",
    "parse_indian_number",
    "parse_monetary_to_paise",
    "parse_subscription_multiple",
    "parse_percent_to_bps",
    "coerce_int",
    "coerce_decimal",
    "coerce_bool",
    "clean_text",
    "sanitize_plaintext",
    "clean_company_name",
    "nfc",
    "UnitParseError",
]
