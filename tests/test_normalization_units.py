"""Tests for ``ipo_portal.normalization.units``.

These are pinned-down examples of every messy upstream input shape we've
observed (or sensibly anticipated) in NSE / BSE / PRIME / Trendlyne /
Moneycontrol snapshots. If any of these break, we know the v2 normalizer
is about to corrupt records. See ``docs/data/EDGE_CASES.md`` for the
rule IDs referenced below.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from ipo_portal.normalization import (
    UnitParseError,
    clean_text,
    coerce_bool,
    coerce_decimal,
    coerce_int,
    nfc,
    parse_indian_date,
    parse_indian_number,
    parse_monetary_to_paise,
    parse_percent_to_bps,
    parse_subscription_multiple,
    sanitize_plaintext,
)
from ipo_portal.normalization.units import parse_indian_instant


class CleanTextTests(unittest.TestCase):
    def test_strips_bom_and_crlf(self) -> None:
        self.assertEqual(clean_text("﻿hello\r\nworld"), "hello world")

    def test_collapses_whitespace(self) -> None:
        self.assertEqual(clean_text("  Yatra   Online  "), "Yatra Online")

    def test_null_sentinels_to_none(self) -> None:
        for sentinel in ("", "-", "--", "—", "NA", "N/A", "null", "NONE", "."):
            self.assertIsNone(clean_text(sentinel), sentinel)

    def test_nfc_normalization(self) -> None:
        composed = "Café"
        decomposed = "Café"
        self.assertEqual(nfc(decomposed), composed)
        self.assertEqual(clean_text(decomposed), composed)


class SanitizePlaintextTests(unittest.TestCase):
    def test_strips_html_tags_and_decodes_entities(self) -> None:
        raw = '<a href="x">Click &amp; pay</a>&nbsp;here'
        self.assertEqual(sanitize_plaintext(raw), "Click & pay here")


class IndianNumberTests(unittest.TestCase):
    def test_plain_int_string(self) -> None:
        self.assertEqual(parse_indian_number("3772000"), Decimal("3772000"))

    def test_indian_comma_format(self) -> None:
        # 1 lakh
        self.assertEqual(parse_indian_number("1,00,000"), Decimal("100000"))
        # 1 crore
        self.assertEqual(parse_indian_number("1,00,00,000"), Decimal("10000000"))

    def test_decimal_value(self) -> None:
        self.assertEqual(parse_indian_number("18.235"), Decimal("18.235"))

    def test_int_input(self) -> None:
        self.assertEqual(parse_indian_number(100000), Decimal("100000"))

    def test_null_sentinel(self) -> None:
        self.assertIsNone(parse_indian_number("-"))
        self.assertIsNone(parse_indian_number(None))

    def test_letters_raise(self) -> None:
        with self.assertRaises(UnitParseError):
            parse_indian_number("12abc")


class CoerceIntTests(unittest.TestCase):
    def test_string_int(self) -> None:
        # NSE returns numbers-as-strings — E.NUM.003.
        self.assertEqual(coerce_int("3772000"), 3_772_000)

    def test_int_passthrough(self) -> None:
        self.assertEqual(coerce_int(42), 42)

    def test_rejects_fraction(self) -> None:
        with self.assertRaises(UnitParseError):
            coerce_int("12.5")

    def test_null_returns_none(self) -> None:
        self.assertIsNone(coerce_int("NA"))


class CoerceBoolTests(unittest.TestCase):
    def test_one_zero_strings(self) -> None:
        # NSE isBse returns "1"/"0".
        self.assertTrue(coerce_bool("1"))
        self.assertFalse(coerce_bool("0"))

    def test_yes_no_strings(self) -> None:
        self.assertTrue(coerce_bool("Yes"))
        self.assertFalse(coerce_bool("N"))

    def test_native_bool(self) -> None:
        self.assertTrue(coerce_bool(True))

    def test_null_returns_none(self) -> None:
        self.assertIsNone(coerce_bool("-"))

    def test_invalid_raises(self) -> None:
        with self.assertRaises(UnitParseError):
            coerce_bool("maybe")


class MonetaryTests(unittest.TestCase):
    def test_plain_rupees_int(self) -> None:
        self.assertEqual(parse_monetary_to_paise(1500, default_unit="rupees"), 150_000)

    def test_lakhs_text(self) -> None:
        # "1500 lakhs" = 15 Cr = 15_00_00_000 ₹ = 1_500_00_00_000 paise
        self.assertEqual(parse_monetary_to_paise("1500 lakhs"), 150_000_000_00)

    def test_crore_text(self) -> None:
        # "1.5 Cr" = 1_50_00_000 ₹ = 1_500_000_000 paise
        self.assertEqual(parse_monetary_to_paise("Rs. 1.5 Cr"), 15_000_000_00)

    def test_inr_prefix(self) -> None:
        self.assertEqual(parse_monetary_to_paise("INR 1,50,00,000"), 1_500_000_000)

    def test_rupee_symbol(self) -> None:
        self.assertEqual(parse_monetary_to_paise("₹500"), 50_000)

    def test_default_unit_lakhs(self) -> None:
        # A raw "1500" with default_unit="lakhs" -> 15 Cr -> 1_500_000_000_0 paise
        self.assertEqual(parse_monetary_to_paise(1500, default_unit="lakhs"), 15_000_000_000)

    def test_unknown_unit_raises(self) -> None:
        with self.assertRaises(UnitParseError):
            parse_monetary_to_paise("100 furlongs")

    def test_null(self) -> None:
        self.assertIsNone(parse_monetary_to_paise(None))
        self.assertIsNone(parse_monetary_to_paise("--"))


class SubscriptionTests(unittest.TestCase):
    def test_plain(self) -> None:
        self.assertEqual(parse_subscription_multiple("7.57"), Decimal("7.5700"))

    def test_with_x_suffix(self) -> None:
        self.assertEqual(parse_subscription_multiple("0.87x"), Decimal("0.8700"))

    def test_precision_preserved(self) -> None:
        # Float would lose this — E.NUM.001.
        self.assertEqual(parse_subscription_multiple("18.2354"), Decimal("18.2354"))

    def test_null(self) -> None:
        self.assertIsNone(parse_subscription_multiple("-"))


class PercentTests(unittest.TestCase):
    def test_percent_with_sign(self) -> None:
        self.assertEqual(parse_percent_to_bps("12.5%"), 1250)

    def test_percent_string_without_sign_big(self) -> None:
        self.assertEqual(parse_percent_to_bps("12.5"), 1250)

    def test_fraction_input(self) -> None:
        # 0.125 fraction -> 12.5% -> 1250 bps
        self.assertEqual(parse_percent_to_bps(0.125), 1250)

    def test_zero(self) -> None:
        self.assertEqual(parse_percent_to_bps("0"), 0)

    def test_null(self) -> None:
        self.assertIsNone(parse_percent_to_bps("-"))


class DateTests(unittest.TestCase):
    def test_dd_mmm_yyyy(self) -> None:
        # NSE format — E.DAT.001.
        self.assertEqual(parse_indian_date("21-May-2026"), date(2026, 5, 21))

    def test_dd_mmm_yyyy_long_month(self) -> None:
        self.assertEqual(parse_indian_date("21-September-2024"), date(2024, 9, 21))

    def test_iso_date(self) -> None:
        self.assertEqual(parse_indian_date("2026-05-21"), date(2026, 5, 21))

    def test_dd_slash(self) -> None:
        self.assertEqual(parse_indian_date("21/05/2026"), date(2026, 5, 21))

    def test_two_digit_year_raises(self) -> None:
        with self.assertRaises(UnitParseError):
            parse_indian_date("21-May-26")

    def test_null(self) -> None:
        self.assertIsNone(parse_indian_date("-"))

    def test_date_passthrough(self) -> None:
        self.assertEqual(parse_indian_date(date(2024, 1, 1)), date(2024, 1, 1))

    def test_garbage_raises(self) -> None:
        with self.assertRaises(UnitParseError):
            parse_indian_date("Tomorrow")

    def test_implausible_year_raises_without_anchor(self) -> None:
        # E.DAT: "0202" is structurally a 4-digit year but implausible.
        with self.assertRaises(UnitParseError):
            parse_indian_date("0202-02-07")

    def test_implausible_year_repaired_with_anchor(self) -> None:
        # Real case: Som Distilleries rights issue close "0202-02-07" was a
        # corrupted "2022-02-07"; the open-date year (2022) anchors the repair.
        self.assertEqual(
            parse_indian_date("0202-02-07", anchor_year=2022),
            date(2022, 2, 7),
        )

    def test_anchor_ignored_for_plausible_year(self) -> None:
        # A valid year is never overridden by the anchor.
        self.assertEqual(
            parse_indian_date("2024-03-05", anchor_year=2022),
            date(2024, 3, 5),
        )

    def test_far_future_year_rejected(self) -> None:
        with self.assertRaises(UnitParseError):
            parse_indian_date("2099-01-01")


class InstantTests(unittest.TestCase):
    def test_naive_iso_is_ist_to_utc(self) -> None:
        # E.DAT.002: naive ISO -> IST -> UTC.
        result = parse_indian_instant("2026-05-21T09:00:00")
        expected = datetime(2026, 5, 21, 3, 30, tzinfo=timezone.utc)
        self.assertEqual(result, expected)

    def test_offset_aware_iso_passthrough(self) -> None:
        result = parse_indian_instant("2026-05-21T09:00:00+00:00")
        expected = datetime(2026, 5, 21, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(result, expected)

    def test_dd_mmm_yyyy_with_time(self) -> None:
        result = parse_indian_instant("21-May-2026 09:30:00")
        # IST 09:30 -> UTC 04:00
        expected = datetime(2026, 5, 21, 4, 0, tzinfo=timezone.utc)
        self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
