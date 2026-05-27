"""Lock v2.0.0: patch the DeepSeek-designed issue schema with gaps.

Run once. Idempotent — if the additions already exist they're untouched.
After this script lands, the schema is considered locked at 2.0.0; future
changes go through the deprecate-not-delete policy
(``docs/data/FUTURE_PROOFING.md`` §1).
"""

from __future__ import annotations

import json
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
ISSUE_SCHEMA = REPO / "docs" / "schema" / "v2" / "issue.schema.json"


ANCHOR_BLOCK = {
    "additionalProperties": False,
    "description": (
        "Anchor investor allocation. Anchors are pre-IPO institutional "
        "allocations that fund up to 60% of the QIB portion (per SEBI). "
        "Reported separately so QIB totals don't double-count "
        "(see EDGE_CASES rule E.SUB.001)."
    ),
    "title": "Anchor Allocation",
    "type": "object",
    "properties": {
        "allotment_paise": {
            "type": "integer",
            "title": "Anchor Allotment (paise)",
            "description": "Total amount allotted to anchor investors in paise.",
            "examples": [50_000_000_000, 12_500_000_000],
            "x-ipo-watch-units": "paise",
            "x-ipo-watch-source-tier": "primary",
            "x-ipo-watch-precedence": 1,
            "x-ipo-watch-rule-ids": ["E.CUR.001", "E.SUB.001"],
            "x-ipo-watch-pii": False,
        },
        "allotment_inr_text": {
            "type": "string",
            "title": "Anchor Allotment (display)",
            "description": "Display-formatted anchor allotment.",
            "examples": ["₹500.00 Cr", "₹125.00 Cr"],
            "x-ipo-watch-units": "INR",
            "x-ipo-watch-source-tier": "primary",
            "x-ipo-watch-precedence": 1,
            "x-ipo-watch-rule-ids": [],
            "x-ipo-watch-pii": False,
        },
        "shares_allotted": {
            "type": "integer",
            "title": "Anchor Shares Allotted",
            "description": "Number of shares allotted to anchor investors.",
            "examples": [3_300_000, 1_200_000],
            "x-ipo-watch-units": "shares",
            "x-ipo-watch-source-tier": "primary",
            "x-ipo-watch-precedence": 1,
            "x-ipo-watch-rule-ids": [],
            "x-ipo-watch-pii": False,
        },
        "investors": {
            "type": "array",
            "title": "Anchor Investors",
            "description": (
                "List of named anchor investors and their allocations. "
                "Populated from RHP/RHP-Anchor disclosure documents."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "shares_allotted"],
                "properties": {
                    "name": {"type": "string", "title": "Investor Name"},
                    "shares_allotted": {"type": "integer", "title": "Shares Allotted"},
                    "amount_paise": {
                        "type": ["integer", "null"],
                        "title": "Amount Allotted (paise)",
                        "x-ipo-watch-units": "paise",
                    },
                    "category": {
                        "type": ["string", "null"],
                        "title": "Investor Category",
                        "examples": ["mutual_fund", "fii", "domestic_institution", "other"],
                    },
                },
            },
            "x-ipo-watch-source-tier": "enrichment",
            "x-ipo-watch-precedence": 3,
            "x-ipo-watch-rule-ids": ["E.SUB.001"],
            "x-ipo-watch-pii": False,
        },
    },
    "required": [],
    "x-ipo-watch-source-tier": "primary",
    "x-ipo-watch-rule-ids": ["E.SUB.001"],
}


CATEGORY_ITEM_SHAPE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["category"],
    "properties": {
        "category": {
            "type": "string",
            "title": "Category",
            "description": (
                "Canonical investor category. Anchor is reported separately "
                "on subscription.anchor (E.SUB.001 — don't double-count)."
            ),
            "enum": [
                "qib_excluding_anchor",
                "nii",
                "nii_gt_10l",
                "nii_lte_10l",
                "retail",
                "employee",
                "shareholder",
                "policyholder",
                "other",
            ],
            "enumDescriptions": {
                "qib_excluding_anchor": "Qualified Institutional Buyers, anchor allocation excluded.",
                "nii": "Non-Institutional Investors (aggregate).",
                "nii_gt_10l": "NII bids over ₹10 lakh per applicant.",
                "nii_lte_10l": "NII bids up to ₹10 lakh per applicant.",
                "retail": "Retail Individual Investors.",
                "employee": "Employee reservation.",
                "shareholder": "Existing-shareholder reservation.",
                "policyholder": "Policyholder reservation (insurance IPOs).",
                "other": "Any other reserved category not enumerated above.",
            },
        },
        "times_x": {
            "type": ["string", "null"],
            "title": "Subscription Times",
            "description": "Decimal-as-string multiple, 4 dp.",
            "examples": ["18.2354", "0.8700"],
            "x-ipo-watch-units": "x",
        },
        "shares_offered": {
            "type": ["integer", "null"],
            "title": "Shares Offered",
            "x-ipo-watch-units": "shares",
        },
        "shares_bid": {
            "type": ["integer", "null"],
            "title": "Shares Bid",
            "x-ipo-watch-units": "shares",
        },
    },
}


FIELD_PROVENANCE_PROPERTY = {
    "type": "object",
    "title": "Field Provenance",
    "description": (
        "Per-field record of which source contributed each disputed value. "
        "Keys are dotted field paths; values record the source, snapshot "
        "timestamp, and the precedence rule that fired. Replayable audit "
        "of every multi-source resolution (FUTURE_PROOFING §3)."
    ),
    "additionalProperties": {
        "type": "object",
        "additionalProperties": False,
        "required": ["source"],
        "properties": {
            "source": {"type": "string"},
            "endpoint": {"type": "string"},
            "snapshot_at": {"type": "string", "format": "date-time"},
            "rule_id": {"type": ["string", "null"]},
        },
    },
}


def patch_issue_schema() -> bool:
    """Return True if any changes were applied."""
    doc = json.loads(ISSUE_SCHEMA.read_text(encoding="utf-8"))
    changed = False

    # 1. subscription.anchor block.
    sub_props = doc["properties"]["subscription"]["properties"]
    if "anchor" not in sub_props:
        sub_props["anchor"] = ANCHOR_BLOCK
        changed = True

    # 2. subscription.categories item shape.
    categories = sub_props.get("categories")
    if categories is not None:
        if categories.get("items") is None or categories["items"] == {}:
            categories["items"] = CATEGORY_ITEM_SHAPE
            changed = True
        elif categories["items"].get("properties") is None:
            categories["items"] = CATEGORY_ITEM_SHAPE
            changed = True

    # 3. Record-level field_provenance property.
    root_props = doc["properties"]
    if "field_provenance" not in root_props:
        root_props["field_provenance"] = FIELD_PROVENANCE_PROPERTY
        changed = True

    # 4. Lock schema version.
    xio = doc.setdefault("x-ipo-watch", {})
    xio["version"] = "2.0.0"
    xio.setdefault("locked_at", "2026-05-23")
    xio["design_notes"] = list(xio.get("design_notes", [])) + [
        "Locked at v2.0.0 on 2026-05-23. Future schema changes follow the "
        "deprecate-not-delete policy (FUTURE_PROOFING §1).",
        "Added subscription.anchor block (E.SUB.001 mitigation).",
        "Added subscription.categories[] enum-locked item shape.",
        "Added record-level field_provenance for multi-source attribution.",
    ]

    if changed or "locked_at" in xio:
        ISSUE_SCHEMA.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return changed


def main() -> None:
    applied = patch_issue_schema()
    print(f"[patch_v2_schema] changes applied: {applied}")


if __name__ == "__main__":
    main()
