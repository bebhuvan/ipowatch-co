"""Validate v2 records against the locked JSON Schema.

This is a second, structural gate on top of the rule-based
``validation_v2`` engine. The rule engine catches *semantic* problems
(missing provenance, bad slug shape); the JSON Schema catches
*structural* drift (wrong type, unknown property, out-of-enum value).

We load ``docs/schema/v2/issue.schema.json`` once and reuse the
compiled validator. A record that fails schema validation is a
``blocking``-tier event — it must not be published with an invalid
shape, because downstream consumers validate against the same schema
and would reject it.

The vendor-extension keys (``x-ipo-watch-*``) and our metadata-envelope
keys live at the schema root alongside the canonical sections. Because
the schema sets ``additionalProperties: false`` on nested objects but
the root carries the envelope, we validate **only the canonical body
sections** against their sub-schemas rather than the whole envelope —
the envelope is validated structurally by the rule engine
(``E.DOC.*``). This keeps the two layers cleanly separated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_SCHEMA_PATH = Path("docs/schema/v2/issue.schema.json")

# Canonical body sections we structurally validate. These are the keys
# under the schema's `properties` that carry real issue data (not the
# metadata envelope). We validate each present section against its
# sub-schema so envelope keys don't trip additionalProperties=false.
VALIDATED_SECTIONS = (
    "identity",
    "pricing",
    "timeline",
    "subscription",
    "listing_performance",
    "documents",
)


@dataclass
class SchemaIssue:
    section: str
    path: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"section": self.section, "path": self.path, "message": self.message}


@dataclass
class SchemaValidator:
    schema_path: Path = DEFAULT_SCHEMA_PATH
    _section_validators: dict[str, Any] = field(default_factory=dict)
    _enabled: bool = True

    def __post_init__(self) -> None:
        try:
            import jsonschema  # noqa: F401
        except ImportError:
            # Schema enforcement is best-effort: if jsonschema isn't
            # installed we degrade gracefully (the rule engine still runs).
            self._enabled = False
            return
        self._load()

    def _load(self) -> None:
        from jsonschema import Draft202012Validator

        if not self.schema_path.exists():
            self._enabled = False
            return
        schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        props = schema.get("properties", {})
        for section in VALIDATED_SECTIONS:
            sub = props.get(section)
            if isinstance(sub, dict):
                self._section_validators[section] = Draft202012Validator(sub)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def validate(self, record: dict[str, Any]) -> list[SchemaIssue]:
        """Return a list of structural issues; empty means valid."""
        if not self._enabled:
            return []
        issues: list[SchemaIssue] = []
        for section, validator in self._section_validators.items():
            value = record.get(section)
            if value is None:
                continue
            for err in validator.iter_errors(value):
                issues.append(
                    SchemaIssue(
                        section=section,
                        path="/".join(str(p) for p in err.absolute_path),
                        message=err.message,
                    )
                )
        return issues


@lru_cache(maxsize=4)
def get_validator(schema_path_str: str = str(DEFAULT_SCHEMA_PATH)) -> SchemaValidator:
    return SchemaValidator(schema_path=Path(schema_path_str))
