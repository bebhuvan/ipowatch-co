"""Tests for the precedence-rules mini-YAML parser and picker."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ipo_portal.normalize_v2.precedence import (
    DEFAULT_TIERS,
    PrecedenceRule,
    PrecedenceRules,
    load_precedence,
)
from ipo_portal.normalize_v2.precedence import _parse_yaml


SAMPLE = """
defaults:
  primary: [nse, bse]
  enrichment: [trendlyne, moneycontrol]

rules:
  - field: pricing.issue_price_paise
    tiers: [nse_of_listing, bse_of_listing, prime]
    reason: >
      Issue price is set by the issuer and reflected by the exchange
      of listing first.
    rule_id: E.SRC.002

  - field: aggregates.sebi.*
    tiers: [datahub]
"""


class ParseYamlTests(unittest.TestCase):
    def test_parses_defaults_and_rules(self) -> None:
        rules = _parse_yaml(SAMPLE)
        self.assertIn("primary", rules.defaults)
        self.assertEqual(rules.defaults["primary"], ("nse", "bse"))
        self.assertEqual(len(rules.rules), 2)

    def test_rule_fields(self) -> None:
        rules = _parse_yaml(SAMPLE)
        first = rules.rules[0]
        self.assertEqual(first.field_path, "pricing.issue_price_paise")
        self.assertEqual(first.tiers, ("nse_of_listing", "bse_of_listing", "prime"))
        self.assertIn("exchange", first.reason or "")
        self.assertEqual(first.rule_id, "E.SRC.002")

    def test_wildcard_rule(self) -> None:
        rules = _parse_yaml(SAMPLE)
        wildcard = rules.rules[1]
        self.assertTrue(wildcard.matches("aggregates.sebi.public_ipo_count"))
        self.assertTrue(wildcard.matches("aggregates.sebi"))
        self.assertFalse(wildcard.matches("pricing.issue_price_paise"))


class PrecedencePickTests(unittest.TestCase):
    def test_first_meaningful_wins(self) -> None:
        rules = _parse_yaml(SAMPLE)
        value, winner = rules.pick(
            "pricing.issue_price_paise",
            {"nse_of_listing": None, "bse_of_listing": "14200", "prime": "14000"},
        )
        self.assertEqual(value, "14200")
        self.assertEqual(winner, "bse_of_listing")

    def test_falls_back_to_default_tier(self) -> None:
        rules = PrecedenceRules()
        value, winner = rules.pick(
            "any.unknown.field",
            {"trendlyne": "x", "bse": "y"},
        )
        self.assertEqual(value, "y")  # bse beats trendlyne in DEFAULT_TIERS
        self.assertEqual(winner, "bse")

    def test_none_when_no_meaningful_value(self) -> None:
        rules = PrecedenceRules()
        value, winner = rules.pick("any.f", {"nse": "", "bse": None})
        self.assertIsNone(value)
        self.assertIsNone(winner)


class LoadFromDiskTests(unittest.TestCase):
    def test_load_from_disk(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.yaml"
            path.write_text(textwrap.dedent(SAMPLE), encoding="utf-8")
            rules = load_precedence(path)
            self.assertEqual(len(rules.rules), 2)


class DefaultTiersTests(unittest.TestCase):
    def test_default_tiers_includes_known_sources(self) -> None:
        for s in ("nse", "bse", "trendlyne", "moneycontrol", "kite"):
            self.assertIn(s, DEFAULT_TIERS)


if __name__ == "__main__":
    unittest.main()
