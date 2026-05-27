"""Upstream schema drift detection.

Run after every fetch cycle. Walks the freshest snapshot for each
``(source, endpoint_group)``, extracts the set of leaf-field paths,
and diffs against the canonical catalog at
``docs/schema/raw_catalog/<source>/<endpoint>.json``.

Three diff classes (``docs/data/EDGE_CASES.md`` ``E.UPS.*``):

* **added**   new paths upstream — log + flag for catalog rerun
* **removed** paths missing from the live snapshot — blocking-tier
* **type_changed** same path, different observed type — warning-tier
  (the normalizer's coerce step usually absorbs it)

All events appended to ``data/reports/upstream_drift.jsonl``. The CLI
exits non-zero if any blocking-tier drift was detected so CI can fail
loud.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from .endpoint_picker import EndpointSample, iter_endpoint_samples
from .metadata import build_envelope


DRIFT_LOG_DEFAULT = Path("data/reports/upstream_drift.jsonl")
CATALOG_ROOT_DEFAULT = Path("docs/schema/raw_catalog")

DriftKind = Literal["added", "removed", "type_changed"]


@dataclass(frozen=True)
class DriftEvent:
    """A single drift observation."""

    source: str
    endpoint_group: str
    endpoint_concrete: str
    snapshot_at: str
    kind: DriftKind
    path: str
    catalog_type: str | None = None
    observed_type: str | None = None
    examples: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected_at": _now_iso(),
            "source": self.source,
            "endpoint_group": self.endpoint_group,
            "endpoint_concrete": self.endpoint_concrete,
            "snapshot_at": self.snapshot_at,
            "kind": self.kind,
            "path": self.path,
            "catalog_type": self.catalog_type,
            "observed_type": self.observed_type,
            "examples": self.examples,
        }


# ---------------------------------------------------------------- walkers


def walk_leaf_paths(body: Any, prefix: str = "$") -> dict[str, str]:
    """Walk a JSON body, returning ``{path: type_name}`` for every leaf.

    Lists are represented by ``[*]`` in the path (we don't index by row).
    The leaf type is the most-specific Python type name: ``"int"``,
    ``"float"``, ``"str"``, ``"bool"``, ``"NoneType"``, ``"list_empty"``.
    """
    seen: dict[str, str] = {}
    _walk(body, prefix, seen)
    return seen


def _walk(value: Any, path: str, seen: dict[str, str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _walk(child, f"{path}.{key}", seen)
        return
    if isinstance(value, list):
        if not value:
            seen[path] = "list_empty"
            return
        # Treat all elements as the same shape; merge types if mixed.
        for child in value:
            _walk(child, f"{path}[*]", seen)
        return
    type_name = type(value).__name__
    existing = seen.get(path)
    if existing is None:
        seen[path] = type_name
    elif existing != type_name:
        # Mixed types observed at the same path; record the union.
        seen[path] = _merge_type(existing, type_name)


def _merge_type(a: str, b: str) -> str:
    if a == b:
        return a
    members = sorted({*a.split("|"), *b.split("|")})
    return "|".join(members)


# ----------------------------------------------------------------- catalog


def load_catalog_fields(catalog_path: Path) -> dict[str, str]:
    """Return ``{path: type}`` for fields in a Phase 1 catalog file."""
    if not catalog_path.exists():
        return {}
    doc = json.loads(catalog_path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for f in doc.get("fields") or []:
        path = f.get("path")
        type_name = f.get("type")
        if not path or not type_name:
            continue
        out[str(path)] = str(type_name)
    return out


def diff_paths(
    catalog: dict[str, str],
    observed: dict[str, str],
) -> tuple[set[str], set[str], dict[str, tuple[str, str]]]:
    """Return ``(added_paths, removed_paths, type_changes)``."""
    catalog_keys = set(catalog)
    observed_keys = set(observed)
    added = observed_keys - catalog_keys
    removed = catalog_keys - observed_keys
    type_changes: dict[str, tuple[str, str]] = {}
    for path in catalog_keys & observed_keys:
        if not _compatible_types(catalog[path], observed[path]):
            type_changes[path] = (catalog[path], observed[path])
    return added, removed, type_changes


_TYPE_COMPATIBILITY = {
    "string": {"str"},
    "integer": {"int", "str"},  # E.NUM.003 — numbers-as-strings is expected
    "number": {"int", "float", "str"},
    "boolean": {"bool", "int", "str"},
    "date": {"str"},
    "datetime": {"str"},
    "currency": {"int", "float", "str"},
    "percent": {"int", "float", "str"},
    "url": {"str"},
    "identifier": {"str", "int"},
    "enum": {"str"},
    "object": {"dict"},
    "array": {"list", "list_empty"},
    "null": {"NoneType"},
}


def _compatible_types(catalog_type: str, observed_type: str) -> bool:
    expected = _TYPE_COMPATIBILITY.get(catalog_type.lower(), set())
    return any(part in expected for part in observed_type.split("|"))


# ----------------------------------------------------------------- runner


@dataclass
class DriftReport:
    events: list[DriftEvent] = field(default_factory=list)
    inspected: int = 0
    missing_catalog: list[tuple[str, str]] = field(default_factory=list)

    @property
    def blocking(self) -> list[DriftEvent]:
        return [e for e in self.events if e.kind == "removed"]


def scan_drift(
    raw_root: Path,
    catalog_root: Path = CATALOG_ROOT_DEFAULT,
    drift_log: Path = DRIFT_LOG_DEFAULT,
) -> DriftReport:
    """Walk all endpoint samples and produce a DriftReport.

    Side effect: append every event to ``drift_log`` (JSONL). The log is
    append-only — never rewrite history.
    """
    report = DriftReport()
    for sample in iter_endpoint_samples(raw_root):
        events = _scan_sample(sample, catalog_root)
        report.events.extend(events)
        report.inspected += 1
        catalog_path = catalog_root / sample.source / f"{sample.endpoint_group}.json"
        if not catalog_path.exists():
            report.missing_catalog.append((sample.source, sample.endpoint_group))
    if report.events:
        _append_log(drift_log, report.events)
    return report


def _scan_sample(sample: EndpointSample, catalog_root: Path) -> list[DriftEvent]:
    catalog_path = catalog_root / sample.source / f"{sample.endpoint_group}.json"
    if not catalog_path.exists():
        return []
    body = _read_body(sample.snapshot_path)
    if body is None:
        return []
    observed = walk_leaf_paths(body)
    catalog = load_catalog_fields(catalog_path)
    added, removed, type_changes = diff_paths(catalog, observed)
    events: list[DriftEvent] = []
    for path in sorted(added):
        events.append(_event(sample, "added", path, observed_type=observed.get(path)))
    for path in sorted(removed):
        events.append(_event(sample, "removed", path, catalog_type=catalog.get(path)))
    for path in sorted(type_changes):
        cat_t, obs_t = type_changes[path]
        events.append(_event(sample, "type_changed", path, catalog_type=cat_t, observed_type=obs_t))
    return events


def _event(
    sample: EndpointSample,
    kind: DriftKind,
    path: str,
    catalog_type: str | None = None,
    observed_type: str | None = None,
) -> DriftEvent:
    return DriftEvent(
        source=sample.source,
        endpoint_group=sample.endpoint_group,
        endpoint_concrete=sample.endpoint_concrete,
        snapshot_at=sample.snapshot_at,
        kind=kind,
        path=path,
        catalog_type=catalog_type,
        observed_type=observed_type,
    )


def _read_body(path: Path) -> Any:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload.get("body")


def _append_log(drift_log: Path, events: Iterable[DriftEvent]) -> None:
    drift_log.parent.mkdir(parents=True, exist_ok=True)
    envelope_header = {
        "envelope_header": build_envelope(
            schema_name="upstream_drift_event.schema",
            schema_version="1.0.0",
            notes="Append-only stream of upstream schema drift events.",
        )
    }
    # We don't write the envelope on every line; we write it once on the
    # first ever creation as a header sentinel.
    if not drift_log.exists():
        with drift_log.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(envelope_header, ensure_ascii=False) + "\n")
    with drift_log.open("a", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
