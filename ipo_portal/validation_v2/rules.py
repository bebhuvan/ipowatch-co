"""Rule primitives for the v2 validation engine.

A ``Rule`` is a stateless callable identified by a stable ``rule_id``
matching the categories in ``docs/data/EDGE_CASES.md`` (``E.ID.NNN``,
``E.CUR.NNN``, …). Rules return zero or more ``Finding`` objects with a
``Severity``. The engine aggregates findings per record and decides
whether the record can publish, must go to ``data/site_v2/quarantine/``,
or is fine-with-warnings.

Rules are registered with the module-level ``registry`` via the
``@register`` decorator. The registry is the single source of truth for
"what checks ran on this build" — the manifest records the registry
fingerprint so we know which version of the ruleset produced any given
``data/site_v2/`` snapshot.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Iterable, Protocol


class Severity(IntEnum):
    """Validation severity tiers.

    The integer values are intentionally ordered so ``max(severities)``
    gives the dominant severity for a record.
    """

    INFO = 10
    WARNING = 20
    ERROR = 30
    BLOCKING = 40

    @property
    def state(self) -> str:
        return {
            Severity.INFO: "clean",
            Severity.WARNING: "clean",
            Severity.ERROR: "review",
            Severity.BLOCKING: "quarantined",
        }[self]


@dataclass(frozen=True)
class Finding:
    """A single validation hit.

    ``evidence`` is whatever values are useful for triage (the bad value,
    the field path, the source). The engine serializes findings into the
    record's ``data_quality`` envelope, so keep ``evidence`` JSON-safe.
    """

    rule_id: str
    severity: Severity
    message: str
    field_path: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.name.lower(),
            "message": self.message,
            "field_path": self.field_path,
            "evidence": self.evidence,
        }


RuleResult = Iterable[Finding]


class Rule(Protocol):
    """The callable contract every check satisfies."""

    rule_id: str
    severity: Severity
    description: str

    def __call__(self, record: dict[str, Any]) -> RuleResult:
        ...


@dataclass
class RuleRegistry:
    """Ordered registry of rules. Keyed by rule_id; iteration is stable."""

    _rules: dict[str, Rule] = field(default_factory=dict)

    def register(self, rule: Rule) -> Rule:
        if rule.rule_id in self._rules:
            raise ValueError(
                f"Duplicate rule_id {rule.rule_id!r}; rule_ids are immutable. "
                "Use a new ID and mark the old one deprecated."
            )
        self._rules[rule.rule_id] = rule
        return rule

    def all(self) -> list[Rule]:
        return list(self._rules.values())

    def fingerprint(self) -> str:
        """SHA1 of (rule_id, severity, description) tuples for the manifest."""
        h = hashlib.sha1()
        for rule_id in sorted(self._rules):
            r = self._rules[rule_id]
            h.update(f"{r.rule_id}|{int(r.severity)}|{r.description}\n".encode())
        return h.hexdigest()


registry = RuleRegistry()


def register(rule_id: str, severity: Severity, description: str) -> Callable[[Callable[[dict[str, Any]], RuleResult]], Rule]:
    """Decorator: turn a plain function into a registered ``Rule``.

    Example::

        @register("E.ID.005", Severity.BLOCKING, "Slug missing short-id")
        def slug_has_short_id(record):
            slug = record.get("slug") or ""
            if "-" not in slug:
                yield Finding(...)
    """

    def decorator(fn: Callable[[dict[str, Any]], RuleResult]) -> Rule:
        rule_obj = _make_rule(rule_id, severity, description, fn)
        registry.register(rule_obj)
        return rule_obj

    return decorator


def _make_rule(rule_id: str, severity: Severity, description: str, fn: Callable[[dict[str, Any]], RuleResult]) -> Rule:
    @dataclass(frozen=True)
    class _Rule:
        rule_id: str
        severity: Severity
        description: str

        def __call__(self, record: dict[str, Any]) -> RuleResult:
            return fn(record) or ()

    return _Rule(rule_id=rule_id, severity=severity, description=description)
