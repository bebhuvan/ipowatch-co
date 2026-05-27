"""Phase 2: synthesize canonical v2 JSON Schemas from the Phase 1 catalog.

Workflow
--------
1. Load every per-endpoint catalog under ``docs/schema/raw_catalog/``.
2. Build a compact "field hints corpus" — for each catalog field with a
   ``canonical_field_hint``, collect (source, endpoint, hint, type,
   units, examples, contamination_risks). Group by hint.
3. Send the corpus to DeepSeek with explicit schema-design instructions
   (versioning, ``$id``, ``$ref``, ``title``, ``description``,
   ``examples``, ``units`` extension, ``precedence`` extension referencing
   ``SOURCE_PRECEDENCE.yaml``, ``nullable_reason``).
4. Validate the returned JSON Schemas parse and are draft-2020-12-ish.
5. Write to ``docs/schema/v2/{issue,company,trajectory}.schema.json``
   plus an aggregated index at ``docs/schema/v2/index.json``.

The user reviews and locks. Phase 4 (normalizer) reads these files.

Design notes
------------
* DeepSeek is allowed to invent canonical field paths it didn't see hints
  for, *only* if the rationale is recorded in ``design_notes[]`` of the
  output document. The reviewer can reject by editing the JSON Schema
  directly; the design will not be regenerated without a ``--rerun`` flag.
* Schemas carry custom extension keys under ``x-ipo-watch.*`` (per JSON
  Schema 2020-12 spec, vendor extensions go on namespaced keys).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..deepseek import DeepSeekClient
from ..storage import write_json
from . import PIPELINE_NAME, __version__
from .metadata import build_envelope, schema_url, utc_now_iso


SCHEMA_OUT_NAME = "v2"
CANONICAL_TARGETS = ("issue", "company", "trajectory")

SYSTEM_PROMPT = """You are a senior data architect designing canonical JSON Schemas for Indian IPO data.

Inputs: a corpus of "field hints" extracted from upstream API endpoints (NSE, BSE, Capital Market, PRIME, Trendlyne, Moneycontrol, IndiaDataHub, RHP extracts). Each hint records: source, endpoint, suggested canonical path, observed type, units, sample values, contamination risks.

Your job: design canonical JSON Schemas (draft 2020-12) for the entities listed in the user message. The schemas will be the canonical reference for IPO Watch — a website and an LLM-readable canonical data source.

Hard requirements:
1. Use JSON Schema draft 2020-12. Each schema has $id, $schema (draft URL), title, description, type=object, properties, required, additionalProperties=false.
2. Every property has: title, description (plain-English), examples (array), type, format (where applicable).
3. Canonical storage units (NON-NEGOTIABLE — see docs/data/FUTURE_PROOFING.md §8):
   - Money: integer paise, field name suffix `_paise`. Add a display sibling `<concept>_inr_text`.
   - Subscription multiples: Decimal as string, field name suffix `_x`.
   - Percentages: integer basis points, field name suffix `_bps`.
   - Dates: ISO 8601 string (`format: "date"` or `format: "date-time"`); all instants UTC with offset.
   - Booleans: native JSON boolean.
4. Use vendor extension keys `x-ipo-watch-units`, `x-ipo-watch-precedence`, `x-ipo-watch-source-tier`, `x-ipo-watch-pii`, `x-ipo-watch-rule-ids` on properties. Don't put these inside `properties`; put them at the same level as `description`.
5. Provenance: include a top-level `field_provenance` object on the schema describing which sources can contribute to which fields.
6. Status / kind enums: use `enum` with explicit `enumDescriptions` (custom keyword) explaining each value.
7. Identifier policy: ISIN preferred; stable slug `<normalized-name>-<6-char-id>`; `aliases[]` for renames.
8. NEVER use floats for money or precise multiples; always integers (paise / bps) or string-encoded Decimal.
9. Group fields into nested objects where meaningful (`pricing`, `timeline`, `subscription`, `listing_performance`, `documents`, `identity`, `classification`).
10. Include the metadata envelope keys ($schema, schema_version, schema_url_self, dataset, dataset_version, generated_at, generated_by, time_zone, currency, language, sources, field_provenance, data_quality, freshness, license) at the root of the issue and company schemas — these are the envelope keys, not body keys.

Output: STRICT JSON object with the keys requested by the user. No markdown fences. No prose outside the JSON."""

USER_TEMPLATE = """Design canonical JSON Schemas for the entities: {targets}.

Use the field-hint corpus below to ground every property in observed upstream data. If a concept appears across multiple sources, dedupe via the precedence tiers (primary > secondary > enrichment). Where catalogue entries flagged contamination_risks, encode the mitigation in the schema (e.g., use integer instead of number, add `pattern` regex, etc.).

OUTPUT JSON SHAPE:
{{
  "schemas": {{
    "<entity_name>": {{
      "$id": "https://ipo-watch.local/schema/v2/<entity>.schema.json",
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "title": "...",
      "description": "...",
      "type": "object",
      "additionalProperties": false,
      "required": [...],
      "properties": {{ ... }},
      "x-ipo-watch": {{
        "version": "2.0.0",
        "field_provenance": {{ ... }},
        "design_notes": ["<choice + rationale>", "..."]
      }}
    }},
    ...
  }},
  "design_notes_global": ["<doc-level rationale>", "..."]
}}

FIELD HINT CORPUS:
{corpus}

EDGE CASE CONTEXT (always available, do not summarize): canonical conventions are in docs/data/FUTURE_PROOFING.md §8 and the contamination rule IDs are namespaced by category in docs/data/EDGE_CASES.md. Reference rule IDs in `x-ipo-watch-rule-ids` arrays where applicable.
"""


@dataclass
class FieldHint:
    """One catalog entry's suggestion about a canonical field."""

    canonical_hint: str
    source: str
    endpoint: str
    upstream_path: str
    upstream_name: str
    type: str
    units: str | None
    examples: list[Any] = field(default_factory=list)
    contamination_risks: list[dict[str, Any]] = field(default_factory=list)
    nullable: bool = True
    is_identifier: bool = False
    is_pii: bool = False
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_hint": self.canonical_hint,
            "source": self.source,
            "endpoint": self.endpoint,
            "upstream_path": self.upstream_path,
            "upstream_name": self.upstream_name,
            "type": self.type,
            "units": self.units,
            "examples": self.examples[:5],
            "contamination_risks": self.contamination_risks,
            "nullable": self.nullable,
            "is_identifier": self.is_identifier,
            "is_pii": self.is_pii,
            "notes": self.notes,
        }

    def to_compact_dict(self) -> dict[str, Any]:
        """Minimal projection for fitting many hints in a prompt budget.

        Keeps the bare essentials: which sources support this canonical
        path, observed type+units, one example, the unique categories of
        contamination risk seen. Drops mitigation strings (the design pass
        re-derives them from EDGE_CASES.md context).
        """
        risk_cats = sorted({r.get("category", "") for r in self.contamination_risks if r.get("category")})
        example = next(iter(self.examples), None)
        return {
            "src": f"{self.source}.{self.endpoint}",
            "name": self.upstream_name,
            "type": self.type,
            "units": self.units,
            "ex": example,
            "risks": risk_cats,
            "pii": self.is_pii or None,
            "id": self.is_identifier or None,
        }


@dataclass
class CompactGroup:
    """One canonical hint with merged contributions across catalog entries."""

    canonical_hint: str
    support_count: int
    sources: list[str]
    representative_type: str
    units_seen: list[str]
    examples: list[Any]
    risk_categories: list[str]
    pii: bool
    is_identifier: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "hint": self.canonical_hint,
            "support": self.support_count,
            "srcs": self.sources,
            "type": self.representative_type,
            "units": self.units_seen,
            "examples": self.examples,
            "risks": self.risk_categories,
            **({"pii": True} if self.pii else {}),
            **({"id": True} if self.is_identifier else {}),
        }


def compact_grouped(grouped: dict[str, list[FieldHint]], max_examples: int = 3) -> list[CompactGroup]:
    """Reduce per-hint detail to one ``CompactGroup`` per canonical path."""
    compact: list[CompactGroup] = []
    for hint, entries in grouped.items():
        sources = sorted({e.source for e in entries})
        type_counter: dict[str, int] = {}
        for e in entries:
            type_counter[e.type] = type_counter.get(e.type, 0) + 1
        rep_type = max(type_counter, key=type_counter.get) if type_counter else "unknown"
        units = sorted({e.units for e in entries if e.units})
        examples: list[Any] = []
        for e in entries:
            for ex in e.examples[:2]:
                if ex not in examples:
                    examples.append(ex)
                if len(examples) >= max_examples:
                    break
            if len(examples) >= max_examples:
                break
        risk_cats: set[str] = set()
        for e in entries:
            for r in e.contamination_risks:
                cat = r.get("category")
                if cat:
                    risk_cats.add(cat)
        compact.append(
            CompactGroup(
                canonical_hint=hint,
                support_count=len(entries),
                sources=sources,
                representative_type=rep_type,
                units_seen=units,
                examples=examples,
                risk_categories=sorted(risk_cats),
                pii=any(e.is_pii for e in entries),
                is_identifier=any(e.is_identifier for e in entries),
            )
        )
    # Sort by support_count (descending) so the most-replicated canonical
    # paths come first; truncation (if needed) drops the long-tail noise.
    compact.sort(key=lambda g: (-g.support_count, g.canonical_hint))
    return compact


def load_catalog_hints(catalog_root: Path) -> list[FieldHint]:
    """Walk ``docs/schema/raw_catalog/`` and gather every field hint."""
    hints: list[FieldHint] = []
    if not catalog_root.exists():
        return hints
    for catalog_file in sorted(catalog_root.glob("*/*.json")):
        try:
            doc = json.loads(catalog_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        source = doc.get("source")
        endpoint = doc.get("endpoint")
        if not source or not endpoint:
            continue
        for f in doc.get("fields") or []:
            hint = f.get("canonical_field_hint")
            if not hint:
                continue
            hints.append(
                FieldHint(
                    canonical_hint=str(hint),
                    source=str(source),
                    endpoint=str(endpoint),
                    upstream_path=str(f.get("path", "")),
                    upstream_name=str(f.get("name", "")),
                    type=str(f.get("type") or ""),
                    units=(f.get("units") or None),
                    examples=list(f.get("examples") or [])[:5],
                    contamination_risks=list(f.get("contamination_risks") or []),
                    nullable=bool(f.get("nullable", True)),
                    is_identifier=bool(f.get("is_identifier", False)),
                    is_pii=bool(f.get("is_pii", False)),
                    notes=(f.get("notes") or None),
                )
            )
    return hints


def group_hints_by_canonical(hints: list[FieldHint]) -> dict[str, list[FieldHint]]:
    grouped: dict[str, list[FieldHint]] = {}
    for h in hints:
        grouped.setdefault(h.canonical_hint, []).append(h)
    return dict(sorted(grouped.items()))


def build_corpus_text(grouped: dict[str, list[FieldHint]], compact: bool = True) -> str:
    """Render the grouped hints as compact JSON for the DeepSeek prompt.

    ``compact=True`` (default) emits one merged entry per canonical hint —
    sufficient for schema design and small enough to fit in DeepSeek's
    64K input window. ``compact=False`` emits full per-hint detail, used
    only for offline inspection.
    """
    if compact:
        compact_groups = compact_grouped(grouped)
        return json.dumps(
            [g.to_dict() for g in compact_groups],
            ensure_ascii=False,
            indent=None,
            separators=(",", ":"),
        )
    corpus: dict[str, list[dict[str, Any]]] = {
        hint: [entry.to_dict() for entry in entries]
        for hint, entries in grouped.items()
    }
    return json.dumps(corpus, ensure_ascii=False, indent=2)


def design_schemas(
    catalog_root: Path,
    schema_root: Path,
    targets: tuple[str, ...] = CANONICAL_TARGETS,
    client: DeepSeekClient | None = None,
    model: str = "deepseek-chat",
) -> Path:
    """Synthesize canonical schemas and write them to ``schema_root``.

    Returns the path of the design-summary file.
    """
    client = client or DeepSeekClient()
    hints = load_catalog_hints(catalog_root)
    if not hints:
        raise RuntimeError(
            f"No catalog hints found under {catalog_root}. Run Phase 1 catalog first."
        )
    grouped = group_hints_by_canonical(hints)
    corpus_text = build_corpus_text(grouped)

    user_prompt = USER_TEMPLATE.format(
        targets=", ".join(targets),
        corpus=corpus_text,
    )

    response = client.chat(
        user=user_prompt,
        system=SYSTEM_PROMPT,
        model=model,
        temperature=0.0,
        response_format="json_object",
        purpose="schema_design",
        extra_telemetry={
            "target_count": len(targets),
            "hint_count": len(hints),
            "grouped_hint_count": len(grouped),
        },
    )
    body = response.json_content
    if not isinstance(body, dict):
        raise RuntimeError(f"Schema-design response was not an object: {response.content[:200]}")
    schemas = body.get("schemas") or {}
    if not isinstance(schemas, dict):
        raise RuntimeError("Response missing 'schemas' object.")

    schema_root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for entity, schema_obj in schemas.items():
        if entity not in targets:
            continue
        if not isinstance(schema_obj, dict):
            continue
        out_path = schema_root / f"{entity}.schema.json"
        write_json(out_path, schema_obj)
        written.append(str(out_path))

    design_summary = {
        **build_envelope(
            schema_name="v2/design_summary.schema",
            schema_version="1.0.0",
            notes=(
                "Index of canonical v2 schemas produced by DeepSeek from the Phase 1 catalog. "
                "Review this file before locking the schemas."
            ),
        ),
        "schema_url_self": "docs/schema/v2/index.json",
        "targets": list(targets),
        "schemas_written": written,
        "hint_count": len(hints),
        "grouped_hint_count": len(grouped),
        "model": response.model,
        "tokens_in": response.prompt_tokens,
        "tokens_out": response.completion_tokens,
        "estimated_cost_usd": response.estimated_cost_usd,
        "design_notes_global": body.get("design_notes_global", []),
        "schema_url": schema_url("v2/index.schema"),
        "generated_at": utc_now_iso(),
        "generated_by": f"{PIPELINE_NAME}/{__version__}",
    }
    index_path = schema_root / "index.json"
    write_json(index_path, design_summary)
    return index_path
