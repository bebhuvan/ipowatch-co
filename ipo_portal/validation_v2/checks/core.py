"""Core validation rules wired up before Phase 4 normalizer exists.

These are the rules whose triggers don't depend on the v2 schema being
finalized — they validate the metadata envelope itself and the document
structure. Field-level rules (E.CUR.*, E.SUB.*, etc.) get added in
Phase 4 once the canonical schema is locked.
"""

from __future__ import annotations

import re
from typing import Any

from ..rules import Finding, Severity, register


_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$")
_ISO_INSTANT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([+-]\d{2}:\d{2}|Z)$")


# ---------------------------------------------------------------- envelope


@register(
    rule_id="E.DOC.001",
    severity=Severity.BLOCKING,
    description="Record is missing the $schema metadata key.",
)
def _has_schema_url(record: dict[str, Any]):
    if not record.get("$schema"):
        yield Finding(
            rule_id="E.DOC.001",
            severity=Severity.BLOCKING,
            message="Record missing $schema URL; consumers cannot validate it.",
            field_path="$schema",
        )


@register(
    rule_id="E.DOC.002",
    severity=Severity.BLOCKING,
    description="Record is missing schema_version.",
)
def _has_schema_version(record: dict[str, Any]):
    if not record.get("schema_version"):
        yield Finding(
            rule_id="E.DOC.002",
            severity=Severity.BLOCKING,
            message="Record missing schema_version.",
            field_path="schema_version",
        )


@register(
    rule_id="E.DOC.003",
    severity=Severity.ERROR,
    description="Record is missing sources[]; provenance cannot be established.",
)
def _has_sources(record: dict[str, Any]):
    sources = record.get("sources")
    if not isinstance(sources, list) or len(sources) == 0:
        yield Finding(
            rule_id="E.DOC.003",
            severity=Severity.ERROR,
            message="Record sources[] is empty; data has no traceable origin.",
            field_path="sources",
        )


@register(
    rule_id="E.DOC.004",
    severity=Severity.WARNING,
    description="generated_at is missing or not ISO 8601 with offset.",
)
def _generated_at_format(record: dict[str, Any]):
    ts = record.get("generated_at")
    if not ts:
        yield Finding(
            rule_id="E.DOC.004",
            severity=Severity.WARNING,
            message="generated_at is missing.",
            field_path="generated_at",
        )
        return
    if not _ISO_INSTANT_RE.match(str(ts)):
        yield Finding(
            rule_id="E.DOC.004",
            severity=Severity.WARNING,
            message=f"generated_at is not ISO 8601 with offset: {ts!r}",
            field_path="generated_at",
            evidence={"observed": ts},
        )


# ----------------------------------------------------------------- slug


@register(
    rule_id="E.ID.005",
    severity=Severity.ERROR,
    description="Slug must be kebab-case with at least one hyphenated short-id segment.",
)
def _slug_structure(record: dict[str, Any]):
    slug = record.get("slug")
    if slug is None:
        return
    text = str(slug)
    if not _SLUG_RE.match(text):
        yield Finding(
            rule_id="E.ID.005",
            severity=Severity.ERROR,
            message=(
                f"Slug {text!r} is not in canonical kebab-case-with-short-id form. "
                "Expected: '<normalized-name>-<6-char-id>'."
            ),
            field_path="slug",
            evidence={"observed": text},
        )
