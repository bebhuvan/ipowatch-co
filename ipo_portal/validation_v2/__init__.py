"""V2 validation engine with severity tiers.

See ``docs/data/FUTURE_PROOFING.md`` §2 and ``docs/data/EDGE_CASES.md``
for the policy this module implements.

Quick reference
---------------
Severity tiers:
    info       — observed but unactionable
    warning    — record publishable; logged for review
    error      — record publishable with state="review"; daily digest
    blocking   — record quarantined; not published

Each check is a ``Rule`` with a stable ``rule_id`` (e.g., ``"E.ID.001"``);
rule IDs never get reused, even when checks are deprecated.
"""

from __future__ import annotations

from .rules import Finding, Rule, RuleResult, Severity, register, registry
from .engine import ValidationEngine, ValidationOutcome, run

# Importing checks registers every concrete rule with the module-level
# registry. Without this, ValidationEngine() runs against an empty
# registry and `ruleset_fingerprint` becomes the SHA1 of "" — a silent
# "no validation ran" bug (E.DOC.* rules would never fire).
from . import checks  # noqa: F401, E402

__all__ = [
    "Finding",
    "Rule",
    "RuleResult",
    "Severity",
    "register",
    "registry",
    "ValidationEngine",
    "ValidationOutcome",
    "run",
]
