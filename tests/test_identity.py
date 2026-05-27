"""Tests for ``ipo_portal.normalize_v2.identity``.

Covers ``E.ID.002`` (name variants), ``E.ID.003`` (PAN validation),
``E.ID.004`` (symbol reuse), ``E.ID.005`` (slug stability).
"""

from __future__ import annotations

import unittest

from ipo_portal.normalize_v2.identity import (
    build_identity,
    is_valid_isin,
    is_valid_pan,
    normalize_name,
    short_id,
    slugify,
    stable_join_key,
)


class NormalizeNameTests(unittest.TestCase):
    def test_ltd_and_limited_collapse(self) -> None:
        # E.ID.002 — the canonical example.
        self.assertEqual(
            normalize_name("Kalana Ispat Ltd"),
            normalize_name("Kalana Ispat Limited"),
        )

    def test_pvt_variants_collapse(self) -> None:
        self.assertEqual(normalize_name("Yatra Online Pvt. Ltd."), "yatra online")
        self.assertEqual(normalize_name("Yatra Online Private Limited"), "yatra online")

    def test_unicode_diacritics_normalized(self) -> None:
        self.assertEqual(normalize_name("Café Holdings"), normalize_name("Cafe Holdings"))

    def test_empty(self) -> None:
        self.assertEqual(normalize_name(""), "")

    def test_company_and_collapse(self) -> None:
        self.assertEqual(
            normalize_name("Tata Steel & Co. Limited"),
            "tata steel",
        )


class IsinTests(unittest.TestCase):
    def test_valid_isin(self) -> None:
        self.assertTrue(is_valid_isin("INE002A01018"))

    def test_invalid_isin(self) -> None:
        self.assertFalse(is_valid_isin("INE002A0101"))
        self.assertFalse(is_valid_isin(""))
        self.assertFalse(is_valid_isin(None))


class PanTests(unittest.TestCase):
    def test_valid_pan(self) -> None:
        self.assertTrue(is_valid_pan("ABCDE1234F"))

    def test_lowercase_accepted_after_uppercase_normalization(self) -> None:
        # We tolerate lowercase input — it's normalized before structural check.
        self.assertTrue(is_valid_pan("abcde1234f"))

    def test_invalid_pan(self) -> None:
        # E.ID.003: PAN regex must reject structurally malformed values.
        self.assertFalse(is_valid_pan("ABCDE12345"))  # missing trailing letter
        self.assertFalse(is_valid_pan("ABCD1234FG"))  # too few leading letters
        self.assertFalse(is_valid_pan(""))
        self.assertFalse(is_valid_pan(None))


class JoinKeyTests(unittest.TestCase):
    def test_isin_wins(self) -> None:
        key = stable_join_key(
            isin="INE002A01018",
            pan="ABCDE1234F",
            listing_year=2024,
            normalized_name="yatra online",
        )
        self.assertEqual(key, "isin:INE002A01018")

    def test_pan_year_fallback(self) -> None:
        key = stable_join_key(
            isin=None,
            pan="ABCDE1234F",
            listing_year=2024,
            normalized_name="yatra online",
        )
        self.assertEqual(key, "pan_year:ABCDE1234F:2024")

    def test_name_year_last_resort(self) -> None:
        key = stable_join_key(
            isin=None,
            pan=None,
            listing_year=2024,
            normalized_name="yatra online",
        )
        self.assertEqual(key, "name_year:yatra online:2024")

    def test_raises_without_any_discriminator(self) -> None:
        with self.assertRaises(ValueError):
            stable_join_key()


class ShortIdTests(unittest.TestCase):
    def test_stable_across_runs(self) -> None:
        self.assertEqual(short_id("isin:INE002A01018"), short_id("isin:INE002A01018"))
        self.assertEqual(len(short_id("isin:INE002A01018")), 6)


class SlugifyTests(unittest.TestCase):
    def test_kebab_with_short_id(self) -> None:
        slug = slugify("yatra online", "isin:INE002A01018")
        self.assertRegex(slug, r"^[a-z0-9]+(?:-[a-z0-9]+)+$")
        self.assertTrue(slug.startswith("yatra-online-"))

    def test_empty_name_falls_back(self) -> None:
        slug = slugify("", "isin:INE002A01018")
        self.assertTrue(slug.startswith("issue-"))


class IdentityTests(unittest.TestCase):
    def test_full_identity_stable_across_name_variants(self) -> None:
        a = build_identity(
            "Kalana Ispat Ltd",
            isin="INE002A01018",
            listing_year=2024,
        )
        b = build_identity(
            "Kalana Ispat Limited",
            isin="INE002A01018",
            listing_year=2024,
        )
        # Same ISIN → same key, short_id, and slug.
        self.assertEqual(a.stable_join_key, b.stable_join_key)
        self.assertEqual(a.short_id, b.short_id)
        self.assertEqual(a.slug, b.slug)

    def test_rename_preserves_short_id(self) -> None:
        original = build_identity(
            "Acme Industries Ltd",
            isin="INE001A01036",
            listing_year=2010,
        )
        renamed = build_identity(
            "AcmeCo International Limited",
            isin="INE001A01036",
            listing_year=2010,
            aliases=(original.slug,),
        )
        # Same ISIN means same short_id (E.ID.005 — slug stability).
        self.assertEqual(original.short_id, renamed.short_id)
        self.assertNotEqual(original.slug, renamed.slug)  # stem changed
        self.assertIn(original.slug, renamed.aliases)


if __name__ == "__main__":
    unittest.main()
