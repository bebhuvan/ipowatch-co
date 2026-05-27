"""End-to-end v2 normalization pipeline.

High-level flow (run by ``python -m ipo_portal.orchestrator normalize``):

1. Load the canonical v2 schemas from ``docs/schema/v2/``.
2. Load source precedence rules from ``docs/data/SOURCE_PRECEDENCE.yaml``.
3. Load dedup rules from ``docs/data/DEDUP_RULES.yaml`` (Phase 3 output).
4. Walk raw snapshots under ``data/raw/``, normalize per-source rows
   into ``Contribution`` objects keyed by stable issue join key.
5. Merge contributions per issue using precedence rules, recording
   ``field_provenance``.
6. Wrap each record in the v2 metadata envelope.
7. Run the validation engine; route blocking-tier records to
   ``data/site_v2/quarantine/``, publishable records to
   ``data/site_v2/issues/by-slug/``.
8. Write indexes, manifest, audit log.

The per-source parsers live in ``normalize_v2.parsers`` and are registered
at runtime before collection begins.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Callable

from ..storage import latest_snapshot, load_latest_snapshots, write_json
from ..validation_v2 import ValidationEngine
from . import precedence as precedence_module
from .precedence import PrecedenceRules


DEFAULT_RAW_ROOT = Path("data/raw")
DEFAULT_OUT_ROOT = Path("data/site_v2")
DEFAULT_SCHEMA_ROOT = Path("docs/schema/v2")


@dataclass(frozen=True)
class IssueJoinKey:
    """Stable identifier across sources for a single canonical issue.

    Construction order (first match wins):
      1. ISIN
      2. (PAN + listing year)
      3. (normalized name + listing year window ± 3 days)
    """

    discriminator: str
    value: str

    def as_slug_seed(self) -> str:
        return f"{self.discriminator}:{self.value}"


@dataclass
class Contribution:
    """A single source row contributing to a canonical issue."""

    source: str
    endpoint: str
    snapshot_at: str
    join_key: IssueJoinKey
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class V2Pipeline:
    """Coordinates the normalize-then-validate-then-write flow."""

    raw_root: Path = DEFAULT_RAW_ROOT
    out_root: Path = DEFAULT_OUT_ROOT
    schema_root: Path = DEFAULT_SCHEMA_ROOT
    precedence: PrecedenceRules = field(default_factory=lambda: PrecedenceRules())
    engine: ValidationEngine = field(default_factory=ValidationEngine)
    issue_parsers: dict[str, Callable[[dict[str, Any]], list[Contribution]]] = field(default_factory=dict)

    def schema(self, name: str) -> dict[str, Any] | None:
        path = self.schema_root / f"{name}.schema.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def collect_contributions(self) -> list[Contribution]:
        """Walk raw snapshots and dispatch to registered per-endpoint parsers.

        Returns a flat list of contributions; the merge phase aggregates
        them by join key. Parsers are registered via the
        ``ipo_portal.normalize_v2.parsers`` package — importing that
        package registers every available source/endpoint handler.
        """
        from .parsers import parser_for
        from .parsers.registry import ParserContext

        contributions: list[Contribution] = []
        for snapshot in load_latest_snapshots(self.raw_root.parent):
            meta = snapshot.get("meta") or {}
            source = meta.get("source")
            endpoint = meta.get("endpoint")
            if not source or not endpoint:
                continue
            parser = parser_for(str(source), str(endpoint))
            if parser is None:
                continue
            ctx = ParserContext(
                source=str(source),
                endpoint=str(endpoint),
                snapshot_at=str(meta.get("fetched_at") or ""),
                snapshot_url=str(meta.get("url") or "") or None,
            )
            contributions.extend(parser(snapshot.get("body"), ctx))
        return contributions

    def merge(self, contributions: list[Contribution]) -> dict[IssueJoinKey, dict[str, Any]]:
        """Group contributions by canonical key and apply precedence per field.

        Grouping is two-level: contributions first cluster by their raw
        ``join_key``, then ``_consolidate`` unions clusters that are the
        same real-world issue under different keys (ISIN-keyed vs
        name+year-keyed). This connects, e.g., a live-IPO record (keyed by
        name+open-year) to its offer-document record (keyed by ISIN or
        filing-year) so the RHP URL lands on one canonical record.
        """
        return self.merge_grouped(_consolidate(contributions))

    def merge_grouped(self, grouped: dict[IssueJoinKey, list[Contribution]]) -> dict[IssueJoinKey, dict[str, Any]]:
        """Apply precedence and inference to already-consolidated groups."""
        merged: dict[IssueJoinKey, dict[str, Any]] = {}
        for key, group in grouped.items():
            record: dict[str, Any] = {}
            provenance: dict[str, dict[str, Any]] = {}
            field_paths = set().union(*(c.fields.keys() for c in group))
            for path in field_paths:
                contributions_by_source = _values_by_source(group, path)
                value, winner = self.precedence.pick(path, contributions_by_source)
                if value is None:
                    continue
                _set_nested(record, path, value)
                if winner:
                    provenance[path] = {
                        "source": winner,
                        "rule_id": "E.SRC.002",
                    }
            # Post-merge inference: derive status from timeline if no parser
            # set it. Many enrichment-tier parsers (offer_documents, document
            # feeds) don't know the status because they're document indexes,
            # not issue-state feeds. The timeline always wins where present.
            _infer_status(record, provenance)
            record.setdefault("field_provenance", provenance)
            merged[key] = record
        return merged


def run_normalize(
    raw_root: Path = DEFAULT_RAW_ROOT,
    out_root: Path = DEFAULT_OUT_ROOT,
    schema_root: Path = DEFAULT_SCHEMA_ROOT,
    precedence_path: Path | None = None,
) -> Path:
    """Run the v2 normalize pipeline end-to-end. Returns ``out_root``.

    Flow: collect contributions via registered parsers → merge by join
    key with precedence rules → wrap in v2 metadata envelope → validate →
    route to ``issues/by-slug/`` (state=clean/review) or ``quarantine/``
    (state=blocking) → write manifest.

    Hash-gated writes mean a re-run with no upstream changes is a no-op
    in git. The validation ruleset fingerprint is recorded in every
    record's ``data_quality`` envelope and in the manifest, so a
    rule-set change is detectable on a subsequent build.
    """
    # Force parser registration by importing the parsers package, then
    # re-scan disk so concrete suffix-keyed endpoints fetched in this same
    # process (e.g. nested per-issue endpoints during a refresh) register.
    from . import parsers as _parsers  # noqa: F401

    _parsers.register_concrete_endpoints()
    from ..orchestrator.metadata import (
        SourceRef,
        build_envelope,
        utc_now_iso,
    )
    from .indexes import build_indexes
    from .schema_check import get_validator

    precedence_rules = precedence_module.load_precedence(
        precedence_path or precedence_module.DEFAULT_RULES_PATH
    )
    pipeline = V2Pipeline(
        raw_root=raw_root,
        out_root=out_root,
        schema_root=schema_root,
        precedence=precedence_rules,
    )
    schema_validator = get_validator(str(schema_root / "issue.schema.json"))

    # Pre-computed sector map (slug -> {sector, industry, source}) from the
    # DeepSeek classifier; applied per-record below.
    from ..orchestrator.sectors import load_sector_map

    _sector_map = load_sector_map()

    contributions = pipeline.collect_contributions()
    old_build_at = os.environ.get("IPO_WATCH_BUILD_AT")
    os.environ["IPO_WATCH_BUILD_AT"] = _deterministic_build_at(contributions)
    grouped = _consolidate(contributions)
    merged = pipeline.merge_grouped(grouped)

    out_root.mkdir(parents=True, exist_ok=True)
    issues_by_slug = out_root / "issues" / "by-slug"
    quarantine_dir = out_root / "quarantine"

    published = 0
    quarantined = 0
    review = 0
    schema_failures = 0
    written_slugs: set[str] = set()
    quarantined_slugs: set[str] = set()

    # Unique by-slug filename per merged group (distinct date-less issues of
    # one company share a slug otherwise → silent overwrite on write).
    slug_by_key = _assign_unique_slugs(merged, grouped)

    for key, body in merged.items():
        slug = slug_by_key.get(key) or body.get("identity", {}).get("slug") or _fallback_slug(key)
        if (body.get("identity") or {}).get("slug") != slug:
            body.setdefault("identity", {})["slug"] = slug
        sources = [
            SourceRef(
                source=c.source,
                endpoint=c.endpoint,
                snapshot_at=c.snapshot_at,
                confidence="primary",
            )
            for c in grouped.get(key, [])
        ]
        envelope = build_envelope(
            schema_name="issue.schema",
            schema_version="2.0.0",
            sources=sources,
            field_provenance=body.pop("field_provenance", {}),
            schema_url_self=f"data/site_v2/issues/by-slug/{slug}.json",
        )
        # Body must include the canonical fields; envelope keys appear first.
        record = {**envelope, **body, "slug": slug}

        _sanitize_record(record)

        # Gains are DERIVED from the canonical merged prices, not taken
        # from a source that may have computed them against its own prices
        # (which can yield impossible values like -163% after precedence
        # picks a different price). Keeps gain ⇔ price internally consistent.
        _recompute_gains(record)

        # Sector/industry: prospectus extract wins; else the DeepSeek
        # sector map (both keyed by the canonical slug, applied here so
        # the build stays deterministic).
        _apply_sector(record, slug, out_root, _sector_map)

        # Structural gate: validate canonical sections against the locked
        # JSON Schema. A structural failure is blocking — we never publish
        # a record whose shape a downstream consumer would reject.
        schema_issues = schema_validator.validate(record)

        merged_record, outcome = pipeline.engine.apply_to_record(record)

        if schema_issues:
            schema_failures += 1
            merged_record.setdefault("data_quality", {})
            dq = merged_record["data_quality"]
            dq["state"] = "quarantined"
            dq.setdefault("errors", []).append(
                {
                    "rule_id": "E.SCHEMA.001",
                    "severity": "blocking",
                    "message": f"{len(schema_issues)} JSON Schema violation(s)",
                    "evidence": [si.to_dict() for si in schema_issues[:10]],
                }
            )
            write_json(quarantine_dir / f"{slug}.json", merged_record)
            quarantined_slugs.add(slug)
            quarantined += 1
            continue

        if outcome.quarantined:
            target = quarantine_dir / f"{slug}.json"
            quarantined_slugs.add(slug)
            quarantined += 1
        else:
            target = issues_by_slug / f"{slug}.json"
            written_slugs.add(slug)
            if outcome.state == "review":
                review += 1
            published += 1
        write_json(target, merged_record)

    # Prune stale records: a slug can change between runs (name cleanup,
    # re-consolidation, dedup), which leaves the old file orphaned. Delete
    # any by-slug/quarantine file not produced this run so the tree exactly
    # reflects the current build (and manifest count matches disk).
    _prune_stale(issues_by_slug, written_slugs)
    _prune_stale(quarantine_dir, quarantined_slugs)

    # Build aggregation indexes (by-year / by-status / by-kind / companies).
    index_stats = build_indexes(out_root)

    # Build subscription trajectories from bid-detail snapshots. Needs the
    # by-slug records (for the alias index), so it runs after indexes.
    from .trajectory_v2 import build_trajectories

    try:
        traj_stats = build_trajectories(raw_root=raw_root, site_v2=out_root)
    except Exception:  # noqa: BLE001 — trajectories are non-fatal to the build
        traj_stats = {"trajectories": 0, "written": 0}

    manifest_path = out_root / "manifest.json"
    manifest = {
        **build_envelope(
            schema_name="manifest.schema",
            schema_version="1.0.0",
            schema_url_self="data/site_v2/manifest.json",
            notes=(
                "Dataset-level manifest for the v2 IPO Watch tree. Every record "
                "in this build was produced by the parsers/precedence/validation "
                "stack documented at docs/data/."
            ),
        ),
        "raw_root": str(raw_root),
        "schema_root": str(schema_root),
        "contributions": len(contributions),
        "issues_total": len(merged),
        "issues_published": published,
        "issues_review_tier": review,
        "issues_quarantined": quarantined,
        "schema_failures": schema_failures,
        "schema_enforcement_enabled": schema_validator.enabled,
        "companies_total": index_stats.get("companies", 0),
        "trajectories_total": traj_stats.get("trajectories", 0),
        "indexes_built": [
            "issues/index.json",
            "issues/by-year/",
            "issues/by-status/",
            "issues/by-kind/",
            "companies/index.json",
            "companies/by-slug/",
        ],
        "parsers_registered": [f"{src}/{ep}" for (src, ep) in _parser_keys()],
        "ruleset_fingerprint": pipeline.engine.rule_registry.fingerprint(),
        "build_completed_at": utc_now_iso(),
    }
    write_json(manifest_path, manifest)
    if old_build_at is None:
        os.environ.pop("IPO_WATCH_BUILD_AT", None)
    else:
        os.environ["IPO_WATCH_BUILD_AT"] = old_build_at
    return out_root


def _parser_keys() -> list[tuple[str, str]]:
    from .parsers import PARSERS

    return sorted(PARSERS.by_key.keys())


def _deterministic_build_at(contributions: list[Contribution]) -> str:
    """Return a stable build instant derived from contributing snapshots."""
    instants: list[datetime] = []
    for c in contributions:
        if not c.snapshot_at:
            continue
        try:
            instants.append(datetime.fromisoformat(c.snapshot_at.replace("Z", "+00:00")))
        except ValueError:
            continue
    if not instants:
        return "1970-01-01T00:00:00+00:00"
    return max(instants).astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _values_by_source(group: list[Contribution], path: str) -> dict[str, Any]:
    """Return one deterministic value per source for ``path``.

    A source can contribute the same canonical field from multiple endpoint
    families. Timed issue-state rows are more authoritative than document
    indexes for identity/status/pricing/timeline, while document paths should
    keep document-index values.
    """
    by_source: dict[str, list[Contribution]] = {}
    for c in group:
        if path in c.fields:
            by_source.setdefault(c.source, []).append(c)
    return {
        source: _preferred_same_source_value(path, candidates)
        for source, candidates in by_source.items()
    }


def _preferred_same_source_value(path: str, candidates: list[Contribution]) -> Any:
    if len(candidates) == 1:
        return candidates[0].fields[path]
    if path.startswith("documents."):
        ranked = sorted(candidates, key=lambda c: (_has_documents(c), c.endpoint), reverse=True)
    else:
        ranked = sorted(candidates, key=lambda c: (_has_timeline(c), not _has_documents(c), c.endpoint), reverse=True)
    return ranked[0].fields[path]


def _has_timeline(contribution: Contribution) -> bool:
    return any(k.startswith("timeline.") for k in contribution.fields)


def _has_documents(contribution: Contribution) -> bool:
    return any(k.startswith("documents.") for k in contribution.fields)


def _consolidate(contributions: list[Contribution]) -> dict[IssueJoinKey, list[Contribution]]:
    """Union same-issue clusters that arrived under different join keys.

    Union rules (conservative — over-merging conflates distinct issues):

    1. **Shared ISIN** — always the same security. Strongest signal.
    2. **Shared (normalized_name, symbol)** — symbol is exchange-assigned;
       safe when both present.
    3. **Document-only absorption** — a record with documents but no
       timeline (a DRHP/RHP "Filed" record) unions into a dated record of
       the same normalized name whose join-key year is within 2 years.
       Only document-only records are absorbed this way, so two dated
       issues by the same company (e.g., IPO then FPO) are NOT merged.

    Returns contributions regrouped under one representative join key per
    canonical cluster.
    """
    from .identity import normalize_name

    by_key: dict[IssueJoinKey, list[Contribution]] = {}
    for c in contributions:
        by_key.setdefault(c.join_key, []).append(c)

    keys = list(by_key.keys())
    parent: dict[IssueJoinKey, IssueJoinKey] = {k: k for k in keys}

    def find(k: IssueJoinKey) -> IssueJoinKey:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a: IssueJoinKey, b: IssueJoinKey) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Extract identity signals per cluster.
    sig: dict[IssueJoinKey, dict[str, Any]] = {}
    for key, group in by_key.items():
        fields: dict[str, Any] = {}
        for c in group:
            fields.update(c.fields)
        has_timeline = any(k.startswith("timeline.") for k in fields)
        has_documents = any(k.startswith("documents.") for k in fields)
        aliases = fields.get("identity.aliases")
        alias_list = aliases if isinstance(aliases, list) else []
        real_year = _real_year_from_fields(fields)
        # A per-issue detail/bid feed has no date, so its join-key year is the
        # FETCH year (an artifact). Identify those (bse:ipo_no alias, or a
        # symbol-discriminated NSE feed) and treat their year as a wildcard so
        # they merge into the real issue, while genuine doc/filing years
        # (offer-doc DRHP dates) still pin a record to its era.
        fetch_year_artifact = key.discriminator == "symbol" or any(
            isinstance(a, str) and a.startswith("bse:ipo_no:") for a in alias_list
        )
        if real_year is not None:
            effective_year = real_year
        elif fetch_year_artifact:
            effective_year = None  # wildcard
        else:
            effective_year = _year_from_join_key(key)
        ipo_no = None
        for a in alias_list:
            if isinstance(a, str) and a.startswith("bse:ipo_no:"):
                ipo_no = a.split(":", 2)[2]
                break
        sig[key] = {
            "isin": fields.get("identity.isin"),
            "symbol": fields.get("identity.symbol"),
            "name": normalize_name(fields.get("identity.company_name") or ""),
            "year": _year_from_join_key(key),
            "real_year": real_year,
            "effective_year": effective_year,
            "issue_type": fields.get("identity.issue_type") or "Others",
            "document_only": has_documents and not has_timeline,
            "ipo_no": ipo_no,
            "aliases": alias_list,
        }

    isin_index: dict[str, list[IssueJoinKey]] = {}
    symbol_index: dict[tuple[str, str], list[IssueJoinKey]] = {}
    name_index: dict[str, list[IssueJoinKey]] = {}
    alias_index: dict[str, list[IssueJoinKey]] = {}
    for key, s in sig.items():
        if s["isin"]:
            isin_index.setdefault(s["isin"], []).append(key)
        if s["symbol"] and s["name"]:
            symbol_index.setdefault((s["name"], s["symbol"]), []).append(key)
        if s["name"]:
            name_index.setdefault(s["name"], []).append(key)
        # Stable exchange-internal aliases (bse:ipo_no:N, bse:scrip_code:N)
        # are globally unique per issue — union records that share one. This
        # is how a date-less per-issue feed (issue_detail / bid_details)
        # merges into the canonical record keyed by name+year.
        for alias in s["aliases"]:
            if isinstance(alias, str) and (
                alias.startswith("bse:ipo_no:") or alias.startswith("bse:scrip_code:")
            ):
                alias_index.setdefault(alias, []).append(key)

    for group_keys in isin_index.values():
        for other in group_keys[1:]:
            union(group_keys[0], other)
    for group_keys in symbol_index.values():
        for other in group_keys[1:]:
            union(group_keys[0], other)
    for group_keys in alias_index.values():
        for other in group_keys[1:]:
            union(group_keys[0], other)

    # Symbol-discriminated keys (NSE per-issue subscription feeds, which are
    # symbol-only) attach to the canonical record sharing that symbol. These
    # feeds are fetched only for active/recent issues, so symbol reuse across
    # eras is not a concern here.
    symbol_owner: dict[str, IssueJoinKey] = {}
    for key, s in sig.items():
        sym = s["symbol"]
        if sym and key.discriminator != "symbol":
            symbol_owner.setdefault(sym.upper(), key)
    for key in keys:
        if key.discriminator == "symbol":
            owner = symbol_owner.get(key.value.upper())
            if owner is not None:
                union(key, owner)

    # Name-cluster union — merge fragments of ONE issue that arrived under
    # different keys (name+year, ISIN, pan_year, fetch-year per-issue feeds).
    # Scoped by (normalized_name, issue_type) so a company's distinct issues
    # of different types (IPO vs OFS vs Buyback vs Rights vs NCD) never
    # collapse together; and within a type, by effective-year era so the
    # same type in eras >2 years apart stays separate. Year-wildcard records
    # (fetch-year per-issue feeds) join only when the (name,type) group has
    # a single era — otherwise they're left to the alias/symbol unions.
    nt_keys: dict[tuple[str, str], list[IssueJoinKey]] = {}
    for key, s in sig.items():
        if s["name"]:
            nt_keys.setdefault((s["name"], s["issue_type"]), []).append(key)
    for (_name, _itype), klist in nt_keys.items():
        # HARD BOUNDARY: a distinct bse:ipo_no is a distinct BSE issue. If
        # this (name, type) group carries two or more different ipo_nos,
        # they are different issues (e.g. an IPO and a later corporate
        # action both typed "Equity") — merging them would fuse unrelated
        # pricing/subscription. Union only within each ipo_no; keep
        # no-ipo_no records as their own clusters rather than guess which
        # issue they belong to. (Same-ipo_no records are already unioned by
        # the alias step above.)
        distinct_ino = {sig[k]["ipo_no"] for k in klist if sig[k]["ipo_no"]}
        if len(distinct_ino) >= 2:
            by_ino: dict[str | None, list[IssueJoinKey]] = {}
            for k in klist:
                by_ino.setdefault(sig[k]["ipo_no"], []).append(k)
            for ks in by_ino.values():
                for other in ks[1:]:
                    union(ks[0], other)
            continue

        # Eras are established ONLY by dated (non-document-only) records. A
        # DRHP filing is part of the same issue as its eventual listing
        # (often years later), so document-only records never spawn their
        # own era — they absorb into the dated issue.
        dated_years = sorted({
            sig[k]["effective_year"]
            for k in klist
            if sig[k]["effective_year"] is not None and not sig[k]["document_only"]
        })
        eras: list[list[int]] = []
        for y in dated_years:
            if eras and y - eras[-1][-1] <= 2:
                eras[-1].append(y)
            else:
                eras.append([y])
        if len(eras) <= 1:
            # One real issue (or none dated) — every fragment of this
            # (name, type) belongs to it: dated, DRHP shadows, fetch-year
            # bid/detail, ISIN/pan_year offer docs.
            for other in klist[1:]:
                union(klist[0], other)
        else:
            # Genuinely multiple dated issues of the same type in eras >2y
            # apart (e.g. repeated OFS/buybacks). Assign each record to the
            # nearest era by its effective year; leave year-less wildcards
            # alone rather than guess.
            era_anchor: dict[int, IssueJoinKey] = {}
            for k in klist:
                ey = sig[k]["effective_year"]
                if ey is None:
                    continue
                era_idx = min(
                    range(len(eras)),
                    key=lambda i: min(abs(ey - y) for y in eras[i]),
                )
                if era_idx in era_anchor:
                    union(era_anchor[era_idx], k)
                else:
                    era_anchor[era_idx] = k

    # Document-only absorption.
    for name, group_keys in name_index.items():
        doc_only = [k for k in group_keys if sig[k]["document_only"]]
        dated = [k for k in group_keys if not sig[k]["document_only"]]
        for dk in doc_only:
            dk_year = sig[dk]["year"]
            best: IssueJoinKey | None = None
            best_gap = 99
            for tk in dated:
                ty = sig[tk]["year"]
                if dk_year and ty and abs(dk_year - ty) <= 2:
                    gap = abs(dk_year - ty)
                    if gap < best_gap:
                        best, best_gap = tk, gap
                elif dk_year is None or ty is None:
                    # No year on one side — only absorb if it's the sole
                    # dated candidate for this name (unambiguous).
                    if len(dated) == 1 and best is None:
                        best = tk
            if best is not None:
                union(dk, best)

    regrouped: dict[IssueJoinKey, list[Contribution]] = {}
    for key, group in by_key.items():
        regrouped.setdefault(find(key), []).extend(group)
    return regrouped


def _year_from_join_key(key: IssueJoinKey) -> int | None:
    """Pull the year out of a ``name_year`` / ``pan_year`` join-key value."""
    parts = key.value.rsplit(":", 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 4:
        return int(parts[1])
    return None


def _real_year_from_fields(fields: dict[str, Any]) -> int | None:
    """The actual issue year from timeline fields (never the fetch year)."""
    for path in (
        "timeline.listing_date",
        "timeline.close_date",
        "timeline.open_date",
        "timeline.drhp_filing_date",
    ):
        val = fields.get(path)
        if val and len(str(val)) >= 4 and str(val)[:4].isdigit():
            year = int(str(val)[:4])
            if 1990 <= year <= 2035:
                return year
    return None


def _fallback_slug(key: IssueJoinKey) -> str:
    safe = "".join(ch if ch.isalnum() else "-" for ch in key.value.lower())
    return f"issue-{safe[:40].strip('-') or 'unknown'}"


def _prune_stale(directory: Path, keep_slugs: set[str]) -> int:
    """Delete ``<dir>/*.json`` whose stem isn't in ``keep_slugs`` this run."""
    if not directory.is_dir():
        return 0
    removed = 0
    for path in directory.glob("*.json"):
        if path.stem not in keep_slugs:
            path.unlink()
            removed += 1
    return removed


def _real_listing_year(body: dict[str, Any]) -> int | None:
    """Listing year from the timeline (a real date, not a fetch-year artifact)."""
    tl = body.get("timeline") or {}
    for fld in ("listing_date", "close_date", "open_date"):
        v = tl.get(fld)
        if isinstance(v, str) and len(v) >= 4 and v[:4].isdigit():
            yr = int(v[:4])
            if 1990 <= yr <= 2035:
                return yr
    return None


def _assign_unique_slugs(
    merged: dict[IssueJoinKey, dict[str, Any]],
    contribs_by_key: dict[IssueJoinKey, list["Contribution"]],
) -> dict[IssueJoinKey, str]:
    """Map each merged group to a unique, stable by-slug filename.

    Date-less per-issue records all carry the same ``name_year:<name>:<fetch
    year>`` join key, so two distinct BSE issues of one company derive the
    same ``identity.slug`` and would clobber each other on write (silent
    loss). We keep them as separate groups (correct — fusing them corrupts
    prices) and instead disambiguate the filename: the record with a real
    listing date / most sources keeps the clean base slug; the rest get a
    slug rehashed from their unique join key. Deterministic across runs.
    """
    from .identity import slugify

    base_of: dict[IssueJoinKey, str] = {}
    by_base: dict[str, list[IssueJoinKey]] = {}
    for key, body in merged.items():
        base = (body.get("identity") or {}).get("slug") or _fallback_slug(key)
        base_of[key] = base
        by_base.setdefault(base, []).append(key)

    assigned: dict[IssueJoinKey, str] = {}
    taken: set[str] = set()
    for base, keys in by_base.items():
        if len(keys) == 1:
            assigned[keys[0]] = base
            taken.add(base)
            continue
        # Richest dated record wins the clean slug; deterministic tiebreak.
        ranked = sorted(
            keys,
            key=lambda k: (
                _real_listing_year(merged[k]) is not None,
                len(contribs_by_key.get(k, [])),
                k.discriminator + ":" + k.value,
            ),
            reverse=True,
        )
        winner = ranked[0]
        assigned[winner] = base
        taken.add(base)
        normalized = (merged[winner].get("identity") or {}).get("normalized_name") or base.rsplit("-", 1)[0]
        for k in ranked[1:]:
            cand = slugify(normalized, f"{k.discriminator}:{k.value}")
            n = 1
            while cand in taken:
                cand = slugify(normalized, f"{k.discriminator}:{k.value}:{n}")
                n += 1
            assigned[k] = cand
            taken.add(cand)
    return assigned


def _set_nested(record: dict[str, Any], dotted_path: str, value: Any) -> None:
    """Set ``record[a][b][c] = value`` given ``"a.b.c"``. Creates dicts as needed."""
    parts = dotted_path.split(".")
    current: dict[str, Any] = record
    for part in parts[:-1]:
        next_node = current.get(part)
        if not isinstance(next_node, dict):
            next_node = {}
            current[part] = next_node
        current = next_node
    current[parts[-1]] = value


def _drop_nested(record: dict[str, Any], dotted_path: str) -> None:
    parts = dotted_path.split(".")
    current: Any = record
    for part in parts[:-1]:
        if not isinstance(current, dict):
            return
        current = current.get(part)
    if isinstance(current, dict):
        current.pop(parts[-1], None)
    prov = record.get("field_provenance")
    if isinstance(prov, dict):
        prov.pop(dotted_path, None)


def _sanitize_record(record: dict[str, Any]) -> None:
    """Remove canonical values that are internally impossible.

    Missing is preferable to confidently publishing stale or unit-corrupt
    values. This runs after precedence and before schema validation.
    """
    identity = record.get("identity") or {}
    pricing = record.get("pricing") or {}
    timeline = record.get("timeline") or {}

    for field in (
        "pricing.issue_price_paise",
        "pricing.price_band_lower_paise",
        "pricing.price_band_upper_paise",
    ):
        val = _get_nested(record, field)
        if isinstance(val, (int, float)) and 0 < val < 100:
            _drop_nested(record, field)

    issue_type = identity.get("issue_type")
    band_semantics_differ = issue_type in ("OFS", "Buyback")
    ip = pricing.get("issue_price_paise")
    lo = pricing.get("price_band_lower_paise")
    hi = pricing.get("price_band_upper_paise")
    if (
        not band_semantics_differ
        and all(isinstance(x, (int, float)) for x in (ip, lo, hi))
        and lo
        and hi
        and not (lo * 0.99 <= ip <= hi * 1.01)
    ):
        _drop_nested(record, "pricing.issue_price_paise")

    open_ = _parse_iso_date(timeline.get("open_date"))
    close = _parse_iso_date(timeline.get("close_date"))
    listing = _parse_iso_date(timeline.get("listing_date"))
    if listing and ((open_ and open_ > listing) or (close and close > listing)):
        _drop_nested(record, "timeline.listing_date")

    _derive_status(record, force=True)


def _get_nested(record: dict[str, Any], dotted_path: str) -> Any:
    current: Any = record
    for part in dotted_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _infer_status(record: dict[str, Any], provenance: dict[str, dict[str, Any]]) -> None:
    """If no parser set ``identity.status``, derive it from the timeline.

    Rules (first match wins):
      * listing_date <= today  →  Listed
      * close_date  <= today   →  Closed
      * open_date   <= today <= close_date  →  Open
      * open_date   >  today   →  Upcoming

    Inferred values are tagged with ``rule_id: "inference"`` in
    field_provenance so consumers can distinguish them from
    source-reported values.
    """
    if (record.get("identity") or {}).get("status"):
        return
    _derive_status(record, provenance=provenance, force=False)


def _derive_status(
    record: dict[str, Any],
    provenance: dict[str, dict[str, Any]] | None = None,
    *,
    force: bool,
) -> None:
    identity = record.get("identity") or {}
    if identity.get("status") and not force:
        return
    timeline = record.get("timeline") or {}
    from ..orchestrator.metadata import utc_now_iso

    today = datetime.fromisoformat(utc_now_iso().replace("Z", "+00:00")).date()
    listing = _parse_iso_date(timeline.get("listing_date"))
    close = _parse_iso_date(timeline.get("close_date"))
    open_ = _parse_iso_date(timeline.get("open_date"))

    status: str | None = None
    if listing and listing <= today:
        status = "Listed"
    elif close and close < today:
        status = "Closed"
    elif open_ and close and open_ <= today <= close:
        status = "Open"
    elif open_ and open_ > today:
        status = "Upcoming"
    elif _has_offer_document(record):
        # No timeline at all, but a prospectus is on record — this is a
        # document-only filing whose outcome we can't confirm from the feed.
        status = "Filed"
    elif _has_subscription_book(record):
        # A per-issue bid book with no date (orphan backfilled detail that
        # didn't name-match a dated record). The book proves the issue ran;
        # we just can't confirm listing → "Closed" is the honest floor.
        status = "Closed"

    if status is None:
        return
    _set_nested(record, "identity.status", status)
    target_provenance = provenance if provenance is not None else record.setdefault("field_provenance", {})
    target_provenance["identity.status"] = {
        "source": "inferred",
        "rule_id": "inference:timeline",
    }


def _apply_sector(record: dict[str, Any], slug: str, out_root: Path, sector_map: dict[str, Any]) -> None:
    """Set ``classification`` from the prospectus extract or the sector map."""
    # 1. Prospectus extract (per-issue file), if present — highest quality.
    prospectus = out_root / "issues" / slug / "prospectus.json"
    if prospectus.exists():
        try:
            pdoc = json.loads(prospectus.read_text(encoding="utf-8"))
            ca = pdoc.get("company_about") or {}
            sector = _leaf_value(ca.get("sector"))
            industry = _leaf_value((pdoc.get("industry_landscape") or {}).get("industry_name"))
            if sector or industry:
                record["classification"] = {
                    "sector": sector, "industry": industry,
                    "sub_industry": None, "source": "rhp",
                }
                return
        except json.JSONDecodeError:
            pass
    # 2. DeepSeek sector map (keyed by normalized company name, stable
    #    across issue merges / slug changes).
    from .identity import normalize_name

    name = (record.get("identity") or {}).get("company_name") or ""
    entry = sector_map.get(normalize_name(name))
    if entry:
        record["classification"] = {
            "sector": entry.get("sector"), "industry": entry.get("industry"),
            "sub_industry": entry.get("sub_industry"), "source": entry.get("source", "deepseek-classification"),
        }


def _leaf_value(leaf: Any) -> Any:
    """Prospectus leaves are {value, ...} provenance blocks; pull value."""
    if isinstance(leaf, dict):
        return leaf.get("value")
    return leaf


def _recompute_gains(record: dict[str, Any]) -> None:
    """Recompute listing/current gain (bps) from the canonical prices.

    A source's pre-computed gain can be inconsistent with the prices that
    won precedence on the merged record (producing impossible <-100%
    values). When we have the issue price and the relevant traded price,
    we recompute so gain ⇔ price always agrees; otherwise we leave any
    source-provided gain in place.
    """
    pricing = record.get("pricing") or {}
    perf = record.get("listing_performance")
    if not isinstance(perf, dict):
        return
    issue = pricing.get("issue_price_paise")
    if not isinstance(issue, (int, float)) or issue <= 0:
        if isinstance(perf, dict):
            perf.pop("listing_gain_bps", None)
            perf.pop("current_gain_bps", None)
        return
    listing_close = perf.get("listing_close_price_paise")
    current = perf.get("current_price_paise")
    if isinstance(listing_close, (int, float)) and listing_close > 0:
        perf["listing_gain_bps"] = _gain_bps(issue, listing_close)
    elif isinstance(listing_close, (int, float)) and listing_close <= 0:
        perf.pop("listing_close_price_paise", None)
        perf.pop("listing_gain_bps", None)
    if isinstance(current, (int, float)) and current > 0:
        perf["current_gain_bps"] = _gain_bps(issue, current)
    elif isinstance(current, (int, float)) and current <= 0:
        perf.pop("current_price_paise", None)
        perf.pop("current_gain_bps", None)


def _gain_bps(issue_price_paise: int | float, traded_price_paise: int | float) -> int:
    value = (Decimal(str(traded_price_paise)) - Decimal(str(issue_price_paise))) * Decimal(10000)
    value = value / Decimal(str(issue_price_paise))
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def _has_offer_document(record: dict[str, Any]) -> bool:
    docs = record.get("documents") or {}
    return any(
        docs.get(k)
        for k in ("drhp_url", "rhp_url", "prospectus_url", "basis_allotment_url")
    )


def _has_subscription_book(record: dict[str, Any]) -> bool:
    sub = record.get("subscription") or {}
    if (sub.get("consolidated") or {}).get("categories"):
        return True
    byx = sub.get("by_exchange") or {}
    return any((byx.get(ex) or {}).get("categories") for ex in ("bse", "nse"))


def _parse_iso_date(value: Any):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except (ValueError, TypeError):
        return None


# Avoid an unused-import lint on latest_snapshot — kept for future per-issue
# fetchers added during Phase 5.
_ = latest_snapshot
