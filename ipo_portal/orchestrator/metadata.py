"""Shared metadata envelope for every v2 output.

This is the documentation contract for the rebuilt dataset
(see task #10 in the rebuild plan). Every JSON written by the orchestrator
or the v2 normalizer carries one of these envelopes so external consumers
(LLM agents, other websites, analysts) can read a single file in isolation
and answer: what is this, where did it come from, when was it generated,
which schema validates it, and how confident should I be in each field?

Envelope keys
-------------
``$schema``            URL to the JSON Schema this document satisfies.
``schema_version``     Semantic version of that schema (e.g. ``"2.0.0"``).
``schema_url_self``    Stable URL where THIS document lives (when known).
``dataset``            Dataset short-name (e.g. ``"ipo-watch.issues"``).
``dataset_version``    Build-time version string. Increments on every build.
``generated_at``       UTC ISO-8601 instant the document was written.
``generated_by``       Tool that produced it (e.g. ``"ipo_portal.orchestrator/0.1.0"``).
``time_zone``          Convention: all date-only fields are IST; all
                       instants are UTC ISO-8601 with offset.
``currency``           Convention: monetary fields are INR unless noted.
``language``           BCP-47 tag. Defaults to ``"en-IN"``.
``sources[]``          List of every source that contributed, each with:
                         - ``source``        (e.g. ``"nse"``)
                         - ``endpoint``      (e.g. ``"ipo_current_issue"``)
                         - ``snapshot_at``   ISO instant of the raw snapshot
                         - ``url``           Origin URL
                         - ``confidence``    ``"primary" | "secondary" | "enrichment"``
``field_provenance``   Map of field-path -> {source, snapshot_at, rule_id}
                       for any field where multiple sources could disagree.
                       Records the precedence rule applied so disputes are
                       always replayable.
``data_quality``       ``{state, errors[], warnings[]}``. State is one of
                       ``"clean" | "review" | "quarantined"``.
``freshness``          Map of ``source -> last_successful_refresh_iso``.
``license``            Usage license / attribution requirements.
``notes``              Optional free-text for human consumers.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from datetime import datetime, timezone
from typing import Any, Literal

from . import PIPELINE_NAME, SCHEMA_BASE_URL, __version__


DATASET_NAME = "ipo-watch"
DEFAULT_LICENSE = (
    "Data is aggregated from public exchange filings (NSE, BSE) and SEBI "
    "disclosures. Source endpoints retain their original copyright. "
    "Derivative aggregations are released under CC-BY-4.0 with attribution "
    "to IPO Watch. Use at your own risk; verify against original filings "
    "before making investment decisions."
)
DEFAULT_TIME_ZONE = "Asia/Kolkata"
DEFAULT_CURRENCY = "INR"
DEFAULT_LANGUAGE = "en-IN"

ConfidenceTier = Literal["primary", "secondary", "enrichment"]
QualityState = Literal["clean", "review", "quarantined"]


@dataclass(frozen=True)
class SourceRef:
    """One source endpoint that contributed to a record.

    ``confidence`` is the tier we trust this source at for the entity:
    - ``primary``     — exchange-of-listing authoritative feed
    - ``secondary``   — non-listing exchange or cross-exchange aggregator
    - ``enrichment``  — derived (Kite quotes, Trendlyne screener, RHP extract)
    """

    source: str
    endpoint: str
    snapshot_at: str
    url: str | None = None
    confidence: ConfidenceTier = "primary"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "endpoint": self.endpoint,
            "snapshot_at": self.snapshot_at,
            "url": self.url,
            "confidence": self.confidence,
        }


def schema_url(schema_name: str) -> str:
    """Stable URL for a v2 schema, used as ``$schema`` in documents."""
    name = schema_name.lstrip("/")
    if not name.endswith(".json"):
        name = f"{name}.json"
    return f"{SCHEMA_BASE_URL}/{name}"


def utc_now_iso() -> str:
    fixed = os.environ.get("IPO_WATCH_BUILD_AT", "").strip()
    if fixed:
        return fixed
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_envelope(
    schema_name: str,
    schema_version: str,
    sources: list[SourceRef] | None = None,
    field_provenance: dict[str, dict[str, Any]] | None = None,
    data_quality: dict[str, Any] | None = None,
    freshness: dict[str, str] | None = None,
    schema_url_self: str | None = None,
    notes: str | None = None,
    license_text: str = DEFAULT_LICENSE,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the standard metadata envelope to be merged into a v2 document.

    Convention: callers ``{**envelope, **body}`` so the envelope keys appear
    first in the serialized JSON and are immediately visible to any reader.
    """
    envelope: dict[str, Any] = {
        "$schema": schema_url(schema_name),
        "schema_version": schema_version,
        "schema_url_self": schema_url_self,
        "dataset": f"{DATASET_NAME}.{schema_name.split('/')[-1].replace('.json', '')}",
        "dataset_version": _dataset_version(),
        "generated_at": utc_now_iso(),
        "generated_by": f"{PIPELINE_NAME}/{__version__}",
        "time_zone": DEFAULT_TIME_ZONE,
        "currency": DEFAULT_CURRENCY,
        "language": DEFAULT_LANGUAGE,
        "sources": [s.to_dict() for s in (sources or [])],
        "field_provenance": field_provenance or {},
        "data_quality": data_quality or {"state": "clean", "errors": [], "warnings": []},
        "freshness": freshness or {},
        "license": license_text,
        "notes": notes,
    }
    if extra:
        envelope.update(extra)
    return envelope


def _dataset_version() -> str:
    """Build-tag style version. Date-stamped so each build is identifiable."""
    fixed = os.environ.get("IPO_WATCH_BUILD_AT", "").strip()
    if fixed:
        try:
            dt = datetime.fromisoformat(fixed.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).strftime("v%Y.%m.%d-%H%M")
        except ValueError:
            pass
    return datetime.now(timezone.utc).strftime("v%Y.%m.%d-%H%M")
