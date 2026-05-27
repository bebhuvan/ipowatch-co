"""Phase 3: pollution / collision audit on the existing v1 site data.

We feed DeepSeek **samples** of the live ``data/site/issues/by-slug/``
records (especially ``data_quality.state == "review"`` ones) and ask
it to:

1. Identify recurring contamination patterns it sees.
2. Map each pattern to one of the rule IDs from
   ``docs/data/EDGE_CASES.md`` (or propose a new ID if novel).
3. Suggest concrete dedup / precedence rules for v2 normalization to
   apply.

Output:
* ``data/reports/pollution_audit.json`` — DeepSeek findings + counts
* ``docs/data/DEDUP_RULES.yaml`` — operator-curated dedup rules (we
  start the file from DeepSeek's proposal; humans edit before Phase 4).

Sampling strategy
-----------------
* Take 100% of records with ``state == "review"`` (sample-capped at 200
  if the count is huge).
* Add a random control set of 20 ``state == "clean"`` records so the
  model can compare healthy vs sick.
* Round-robin across issue kinds so we cover IPO/OFS/rights/buyback etc.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..deepseek import DeepSeekClient
from ..storage import write_json
from . import PIPELINE_NAME, __version__
from .metadata import build_envelope, schema_url, utc_now_iso


DEFAULT_SITE_ROOT = Path("data/site")
DEFAULT_REPORT_PATH = Path("data/reports/pollution_audit.json")
DEFAULT_RULES_PATH = Path("docs/data/DEDUP_RULES.yaml")

SAMPLE_REVIEW_CAP = 200
CONTROL_SAMPLE_SIZE = 20
RANDOM_SEED = 20260523

SYSTEM_PROMPT = """You are an expert auditor reviewing aggregated IPO records for a finance dataset that aspires to be canonical for Indian IPOs.

You are given two sets of records:
1. REVIEW set: records the existing pipeline flagged with data_quality.state="review".
2. CONTROL set: records flagged "clean" — for contrast.

Your job: enumerate concrete contamination patterns you see, mapped to a stable rule_id taxonomy. Categories are:

- E.ID  identifier collision / stability
- E.CUR currency / units / magnitude
- E.DAT date / time / timezone
- E.NUM number parsing
- E.SUB subscription / bid book
- E.SRC source merging / precedence
- E.STA status / state machine
- E.HTM HTML / encoding / locale
- E.SNP snapshot lifecycle
- E.PAG pagination / dropdown
- E.UPS upstream schema drift
- E.LEG regulatory / legal

For each pattern, propose a concrete dedup or normalization rule the new v2 pipeline must implement to prevent recurrence. Output STRICT JSON only — no markdown fences."""

USER_TEMPLATE = """Audit the following records and emit a JSON report.

OUTPUT SHAPE:
{{
  "summary": "<one-paragraph overview of the audit findings>",
  "patterns": [
    {{
      "pattern_id": "<short slug, e.g., 'company-suffix-variants'>",
      "rule_id_category": "E.ID | E.CUR | E.DAT | E.NUM | E.SUB | E.SRC | E.STA | E.HTM | E.SNP | E.PAG | E.UPS | E.LEG",
      "rule_id_specific": "<e.g., E.ID.002 if it matches an existing rule, else 'new'>",
      "description": "<plain English>",
      "evidence_slugs": ["<slug1>", "<slug2>"],
      "estimated_record_count": <integer>,
      "severity": "info | warning | error | blocking",
      "suggested_v2_rule": {{
        "rule_id": "E.<CAT>.<NNN>",
        "trigger": "<when to fire>",
        "action": "<concrete normalizer/validator behavior>"
      }}
    }}
  ],
  "dedup_rules_yaml": "<a YAML block we can copy into docs/data/DEDUP_RULES.yaml>",
  "open_questions": ["<things that need human review>"],
  "audit_metadata": {{
    "review_count_seen": <int>,
    "control_count_seen": <int>
  }}
}}

REVIEW RECORDS (sampled):
{review_records}

CONTROL RECORDS (random sample of clean records for comparison):
{control_records}
"""


@dataclass
class AuditSample:
    review: list[dict[str, Any]] = field(default_factory=list)
    control: list[dict[str, Any]] = field(default_factory=list)


def collect_samples(
    site_root: Path,
    review_cap: int = SAMPLE_REVIEW_CAP,
    control_size: int = CONTROL_SAMPLE_SIZE,
    seed: int = RANDOM_SEED,
) -> AuditSample:
    """Build a representative sample of review + control records."""
    by_slug = site_root / "issues" / "by-slug"
    if not by_slug.exists():
        raise FileNotFoundError(f"by-slug dir not found: {by_slug}")
    review: list[dict[str, Any]] = []
    clean: list[dict[str, Any]] = []
    for path in sorted(by_slug.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        state = (doc.get("data_quality") or {}).get("state")
        if state == "review":
            review.append(_compact_record(doc))
        elif state == "clean":
            clean.append(_compact_record(doc))

    rng = random.Random(seed)
    if len(review) > review_cap:
        review = rng.sample(review, review_cap)
    if len(clean) > control_size:
        clean = rng.sample(clean, control_size)
    return AuditSample(review=review, control=clean)


def _compact_record(doc: dict[str, Any]) -> dict[str, Any]:
    """Shrink a v1 record to the fields useful for audit.

    We strip large fields (raw exchange_details, document blobs) but keep
    identifiers, classification, pricing, timeline, sources, data_quality.
    """
    keep_keys = {
        "id",
        "title",
        "slug",
        "url_path",
        "classification",
        "company",
        "pricing",
        "timeline",
        "issue_size",
        "subscription",
        "listing_performance",
        "sources",
        "data_quality",
        "updated_at",
    }
    return {k: doc[k] for k in keep_keys if k in doc}


def run_audit(
    site_root: Path,
    report_path: Path = DEFAULT_REPORT_PATH,
    rules_path: Path = DEFAULT_RULES_PATH,
    review_cap: int = SAMPLE_REVIEW_CAP,
    control_size: int = CONTROL_SAMPLE_SIZE,
    client: DeepSeekClient | None = None,
    model: str = "deepseek-chat",
) -> Path:
    """Run the audit and write artifacts. Returns report_path."""
    client = client or DeepSeekClient()
    sample = collect_samples(site_root, review_cap=review_cap, control_size=control_size)

    user_prompt = USER_TEMPLATE.format(
        review_records=json.dumps(sample.review, ensure_ascii=False, indent=2),
        control_records=json.dumps(sample.control, ensure_ascii=False, indent=2),
    )

    response = client.chat(
        user=user_prompt,
        system=SYSTEM_PROMPT,
        model=model,
        temperature=0.0,
        response_format="json_object",
        purpose="audit:pollution",
        extra_telemetry={
            "review_count": len(sample.review),
            "control_count": len(sample.control),
        },
    )
    body = response.json_content
    if not isinstance(body, dict):
        raise RuntimeError(f"Audit response was not an object: {response.content[:200]}")

    envelope = build_envelope(
        schema_name="audit/pollution.schema",
        schema_version="1.0.0",
        notes=(
            "DeepSeek-generated audit of contamination patterns in the v1 "
            "site output. Review the patterns and copy approved dedup rules "
            "into docs/data/DEDUP_RULES.yaml."
        ),
    )
    document = {
        **envelope,
        "schema_url_self": "data/reports/pollution_audit.json",
        "schema_url": schema_url("audit/pollution.schema"),
        "review_count": len(sample.review),
        "control_count": len(sample.control),
        "model": response.model,
        "tokens_in": response.prompt_tokens,
        "tokens_out": response.completion_tokens,
        "estimated_cost_usd": response.estimated_cost_usd,
        "generated_at": utc_now_iso(),
        "generated_by": f"{PIPELINE_NAME}/{__version__}",
        **body,
    }
    write_json(report_path, document)

    dedup_yaml = body.get("dedup_rules_yaml")
    if isinstance(dedup_yaml, str) and dedup_yaml.strip():
        rules_path.parent.mkdir(parents=True, exist_ok=True)
        rules_path.write_text(
            "# Auto-generated dedup rules proposal — review and edit before Phase 4.\n"
            f"# Source: data/reports/pollution_audit.json (generated {utc_now_iso()}).\n\n"
            + dedup_yaml.strip()
            + "\n",
            encoding="utf-8",
        )
    return report_path
