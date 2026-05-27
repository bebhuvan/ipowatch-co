# 001 — Canonical storage units for money, multiples, percentages

**Status:** Accepted
**Date:** 2026-05-23
**Authors:** Bhuvanesh R + IPO Watch contributors

## Context

Indian IPO data flows in from a half-dozen sources, each with its own
unit conventions:

* NSE returns money as raw integer rupees in some fields, text like
  `"Rs. 1.5 Cr"` in others, and numbers-as-strings (`"3772000"`) almost
  everywhere.
* BSE mixes lakhs and rupees within a single row (issue size in
  rupees, lot size in shares).
* Trendlyne reports listing gain as a fractional percent (`0.125` =
  12.5%) in some endpoints and a percent-as-string (`"12.5%"`) in
  others.
* Subscription multiples have variable precision — a `0.87x` becomes
  `0.86999...` if stored as `float` and `18.235x` becomes `18.23`.

Without a canonical storage form, every downstream consumer (the
website, an LLM agent, partner sites) has to re-derive units and risks
being off by a factor of 10⁵ or 10⁷ (`E.CUR.001`).

## Decision

All v2 records store these field types in canonical units:

| Concept | Storage | Field name suffix |
|---|---|---|
| Money | Integer **paise** (₹ × 100) | `_paise` |
| Subscription multiple | `Decimal` (4 decimal places) serialized as string | `_x` |
| Percentage | Integer **basis points** (1% == 100) | `_bps` |
| Dates (date-only) | ISO 8601 `YYYY-MM-DD` | (no suffix) |
| Instants | ISO 8601 with explicit UTC offset | (no suffix) |

Boundary parsers in `ipo_portal/normalization/units.py` convert from
every observed upstream form to the canonical storage form. Display
formatting (`"₹15.00 Cr"`, `"7.57x"`) happens at the site-build layer,
not in storage.

No field of these types is ever stored as a Python `float`. Floats are
used only for transient computation that gets re-quantized before
serialization.

## Alternatives considered

1. **Store everything as upstream-typed strings**, defer conversion to
   consumers. Rejected: forces every consumer (including LLMs and
   third parties) to rewrite the same parsing logic, and downstream
   bugs are invisible to us.
2. **Store money as `Decimal` rupees instead of integer paise.**
   Rejected: serializing `Decimal` to JSON is lossy unless we
   string-encode, which throws away the typing advantage. Integer
   paise is unambiguous and arithmetic-safe.
3. **Store percentages as fractional decimals (0.125 for 12.5%).**
   Rejected: too many sources publish "percent" without the explicit
   denominator; encoding as `_bps` makes the unit visible in the field
   name and eliminates ambiguity.

## Consequences

* Normalizer is more code (one boundary parser per concept) but
  consumers never reinterpret units.
* Display layer requires a small set of helpers to format the canonical
  units back to human-readable text. This is a feature: localization
  is centralized.
* Schema fields use long, explicit names like
  `pricing.price_band.upper_paise`. Worth it — the field name carries
  the unit contract.
* Anyone adding a new monetary field MUST follow the `_paise` suffix
  convention. A validation rule (`E.CUR.001`) enforces this in
  `validate_v2`.

## Related

* `docs/data/EDGE_CASES.md` rule IDs: `E.CUR.001`, `E.CUR.002`,
  `E.NUM.001`, `E.NUM.002`, `E.NUM.003`, `E.DAT.001`, `E.DAT.002`.
* `docs/data/FUTURE_PROOFING.md` §8.
* `ipo_portal/normalization/units.py` — boundary parsers.
* `tests/test_normalization_units.py` — 48 pinned-down examples.
