"""Run validation rules over a record and decide its disposition.

The engine collects every ``Finding`` from every rule, computes the
dominant severity, and returns a ``ValidationOutcome`` that the v2
normalizer uses to decide:

* publish to ``data/site_v2/issues/by-slug/`` (severity <= WARNING)
* publish to ``data/site_v2/issues/by-slug/`` with ``state="review"`` (ERROR)
* route to ``data/site_v2/quarantine/`` instead (BLOCKING)

The outcome also injects findings into the record's ``data_quality``
envelope so consumers can introspect what fired without re-running the
engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from ipo_portal.orchestrator.metadata import utc_now_iso

from .rules import Finding, RuleRegistry, Severity, registry as default_registry


@dataclass
class ValidationOutcome:
    """Result of running the engine on a single record."""

    findings: list[Finding] = field(default_factory=list)
    dominant_severity: Severity = Severity.INFO
    state: str = "clean"
    quarantined: bool = False

    @property
    def blocking_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.BLOCKING]

    @property
    def error_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARNING]

    @property
    def info(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.INFO]


@dataclass
class ValidationEngine:
    """Runs all registered rules and aggregates findings."""

    rule_registry: RuleRegistry = field(default_factory=lambda: default_registry)

    def run(self, record: dict[str, Any]) -> ValidationOutcome:
        all_findings: list[Finding] = []
        for rule in self.rule_registry.all():
            try:
                produced = list(rule(record))
            except Exception as exc:  # pragma: no cover — guard against rule bugs
                produced = [
                    Finding(
                        rule_id="E.ENG.001",
                        severity=Severity.ERROR,
                        message=f"Rule {rule.rule_id} crashed: {exc!r}",
                        evidence={"rule_id": rule.rule_id, "error": str(exc)},
                    )
                ]
            all_findings.extend(produced)

        if all_findings:
            dominant = max(f.severity for f in all_findings)
        else:
            dominant = Severity.INFO
        outcome = ValidationOutcome(
            findings=all_findings,
            dominant_severity=dominant,
            state=dominant.state,
            quarantined=dominant is Severity.BLOCKING,
        )
        return outcome

    def apply_to_record(self, record: dict[str, Any]) -> tuple[dict[str, Any], ValidationOutcome]:
        """Run rules and inject findings into the record's data_quality envelope.

        Returns ``(record_with_findings, outcome)``. The returned record is a
        shallow copy with the ``data_quality`` key replaced — callers can
        merge it into the v2 record body before serialization.
        """
        outcome = self.run(record)
        envelope = {
            "state": outcome.state,
            "evaluated_at": _now_iso(),
            "ruleset_fingerprint": self.rule_registry.fingerprint(),
            "errors": [f.to_dict() for f in outcome.error_findings + outcome.blocking_findings],
            "warnings": [f.to_dict() for f in outcome.warnings],
            "info": [f.to_dict() for f in outcome.info],
        }
        merged = dict(record)
        merged["data_quality"] = envelope
        return merged, outcome


def run(record: dict[str, Any], engine: ValidationEngine | None = None) -> ValidationOutcome:
    """Convenience: run the default engine against a record."""
    return (engine or ValidationEngine()).run(record)


def _now_iso() -> str:
    return utc_now_iso()


def iter_findings(records: Iterable[dict[str, Any]], engine: ValidationEngine | None = None) -> Iterable[tuple[dict[str, Any], ValidationOutcome]]:
    """Yield (record, outcome) for each record. Useful for batch reporting."""
    eng = engine or ValidationEngine()
    for rec in records:
        yield rec, eng.run(rec)
