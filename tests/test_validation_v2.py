"""Tests for the v2 validation engine and core rules."""

from __future__ import annotations

import unittest
from typing import Any

from ipo_portal.validation_v2 import (
    Finding,
    Severity,
    ValidationEngine,
    register,
    registry,
)
from ipo_portal.validation_v2.rules import RuleRegistry


VALID_RECORD: dict[str, Any] = {
    "$schema": "https://ipo-watch.local/schema/v2/issue.schema.json",
    "schema_version": "2.0.0",
    "generated_at": "2026-05-23T12:00:00+00:00",
    "sources": [
        {
            "source": "nse",
            "endpoint": "ipo_current_issue",
            "snapshot_at": "2026-05-23T11:00:00+00:00",
            "confidence": "primary",
        }
    ],
    "slug": "yatra-online-abc123",
}


class CoreRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        # Re-import to ensure checks are registered (idempotent — register
        # raises on duplicates, so we only do this once for the real registry).
        import ipo_portal.validation_v2.checks  # noqa: F401
        self.engine = ValidationEngine()

    def test_clean_record_passes(self) -> None:
        outcome = self.engine.run(VALID_RECORD)
        self.assertEqual(outcome.dominant_severity, Severity.INFO)
        self.assertEqual(outcome.state, "clean")
        self.assertFalse(outcome.quarantined)
        self.assertEqual(outcome.findings, [])

    def test_missing_schema_blocks(self) -> None:
        rec = dict(VALID_RECORD)
        rec.pop("$schema")
        outcome = self.engine.run(rec)
        self.assertEqual(outcome.dominant_severity, Severity.BLOCKING)
        self.assertTrue(outcome.quarantined)
        self.assertEqual(outcome.state, "quarantined")
        ids = [f.rule_id for f in outcome.findings]
        self.assertIn("E.DOC.001", ids)

    def test_missing_sources_errors(self) -> None:
        rec = dict(VALID_RECORD)
        rec["sources"] = []
        outcome = self.engine.run(rec)
        self.assertEqual(outcome.dominant_severity, Severity.ERROR)
        self.assertEqual(outcome.state, "review")

    def test_bad_slug_errors(self) -> None:
        rec = dict(VALID_RECORD)
        rec["slug"] = "Bad Slug With Spaces"
        outcome = self.engine.run(rec)
        self.assertIn("E.ID.005", [f.rule_id for f in outcome.findings])

    def test_bad_timestamp_warns(self) -> None:
        rec = dict(VALID_RECORD)
        rec["generated_at"] = "2026-05-23 12:00:00"  # no T, no offset
        outcome = self.engine.run(rec)
        self.assertEqual(outcome.dominant_severity, Severity.WARNING)
        self.assertEqual(outcome.state, "clean")  # warning still publishable

    def test_apply_to_record_injects_envelope(self) -> None:
        merged, outcome = self.engine.apply_to_record(VALID_RECORD)
        self.assertIn("data_quality", merged)
        dq = merged["data_quality"]
        self.assertEqual(dq["state"], "clean")
        self.assertIn("ruleset_fingerprint", dq)
        self.assertEqual(dq["errors"], [])
        self.assertEqual(dq["warnings"], [])


class RegistryTests(unittest.TestCase):
    def test_duplicate_rule_id_raises(self) -> None:
        local = RuleRegistry()

        def make(reg):
            @register(  # noqa: B902 — using real decorator against local registry
                rule_id="E.TEST.001",
                severity=Severity.WARNING,
                description="test",
            )
            def _check(rec):
                return ()

            return _check

        # The real `register` decorator uses the module-level registry, so we
        # exercise the RuleRegistry.register API directly here.
        from ipo_portal.validation_v2.rules import _make_rule  # type: ignore

        rule = _make_rule("E.TEST.999", Severity.INFO, "test", lambda r: ())
        local.register(rule)
        with self.assertRaises(ValueError):
            local.register(rule)

    def test_fingerprint_changes_on_modification(self) -> None:
        from ipo_portal.validation_v2.rules import _make_rule  # type: ignore

        reg1 = RuleRegistry()
        reg2 = RuleRegistry()
        reg1.register(_make_rule("E.TEST.001", Severity.INFO, "a", lambda r: ()))
        reg2.register(_make_rule("E.TEST.001", Severity.INFO, "b", lambda r: ()))
        self.assertNotEqual(reg1.fingerprint(), reg2.fingerprint())


if __name__ == "__main__":
    unittest.main()
