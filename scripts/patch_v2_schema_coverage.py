"""Schema patch: add the per-issue coverage blocks the audit found missing.

Adds (idempotent):
* root ``parties``      — BRLM/co-BRLM, registrar, sponsor bank, syndicate.
* root ``book_building``— daily subscription progression + demand schedule.
* ``subscription.consolidated`` — NSE+BSE combined final category book.
* ``subscription.by_exchange``  — per-exchange final category book (we keep
  BOTH the consolidated and the per-exchange books, per the data owner).

New blocks are intentionally permissive (no additionalProperties:false on
the nested objects) so parsers can populate them flexibly; the root stays
strict.
"""

from __future__ import annotations

import json
from pathlib import Path

SCHEMA = Path(__file__).resolve().parent.parent / "docs" / "schema" / "v2" / "issue.schema.json"


def _category_array(title: str) -> dict:
    return {
        "type": "array",
        "title": title,
        "description": "Final per-category subscription book.",
        "items": {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "shares_offered": {"type": ["integer", "null"]},
                "shares_bid": {"type": ["integer", "null"]},
                "times_x": {"type": ["string", "null"]},
            },
        },
    }


PARTIES = {
    "type": "object",
    "title": "Parties (dealmakers)",
    "description": "Intermediaries: lead managers, registrar, sponsor bank, syndicate.",
    "properties": {
        "lead_managers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Book Running Lead Manager(s).",
            "x-ipo-watch-source-tier": "primary",
        },
        "co_lead_managers": {"type": "array", "items": {"type": "string"}},
        "registrar": {"type": ["string", "null"], "description": "Registrar / RTA."},
        "sponsor_bank": {"type": ["string", "null"]},
        "syndicate_members": {"type": "array", "items": {"type": "string"}},
    },
    "x-ipo-watch-source-tier": "primary",
    "x-ipo-watch-rule-ids": ["E.HTM"],
}

BOOK_BUILDING = {
    "type": "object",
    "title": "Book building",
    "description": "Day-by-day subscription progression and the demand schedule (price→quantity).",
    "properties": {
        "daily_subscription": {
            "type": "array",
            "description": "Subscription multiple by bidding day.",
            "items": {
                "type": "object",
                "properties": {
                    "day": {"type": "integer"},
                    "times_x": {"type": ["string", "null"]},
                },
            },
        },
        "demand_schedule": {
            "type": "array",
            "description": "Demand at each price point (the book).",
            "items": {
                "type": "object",
                "properties": {
                    "price_paise": {"type": ["integer", "null"]},
                    "quantity": {"type": ["integer", "null"]},
                    "cumulative_quantity": {"type": ["integer", "null"]},
                },
            },
            "x-ipo-watch-units": "paise/shares",
        },
    },
    "x-ipo-watch-source-tier": "primary",
}


def main() -> None:
    doc = json.loads(SCHEMA.read_text(encoding="utf-8"))
    props = doc["properties"]
    changed = False

    if "parties" not in props:
        props["parties"] = PARTIES
        changed = True
    if "book_building" not in props:
        props["book_building"] = BOOK_BUILDING
        changed = True

    sub = props["subscription"]["properties"]
    if "consolidated" not in sub:
        sub["consolidated"] = {
            "type": "object",
            "title": "Consolidated book (NSE+BSE)",
            "description": "Combined final subscription across both exchanges.",
            "properties": {
                "categories": _category_array("Consolidated categories"),
                "total_times_x": {"type": ["string", "null"]},
            },
        }
        changed = True
    if "by_exchange" not in sub:
        sub["by_exchange"] = {
            "type": "object",
            "title": "Per-exchange book",
            "description": "Final subscription book kept per exchange (NSE / BSE).",
            "properties": {
                "nse": {
                    "type": "object",
                    "properties": {
                        "categories": _category_array("NSE categories"),
                        "total_times_x": {"type": ["string", "null"]},
                    },
                },
                "bse": {
                    "type": "object",
                    "properties": {
                        "categories": _category_array("BSE categories"),
                        "total_times_x": {"type": ["string", "null"]},
                    },
                },
            },
        }
        changed = True

    xio = doc.setdefault("x-ipo-watch", {})
    notes = list(xio.get("design_notes", []))
    note = "Coverage patch 2026-05-23: added parties, book_building, subscription.consolidated + by_exchange."
    if note not in notes:
        notes.append(note)
        xio["design_notes"] = notes
        changed = True

    SCHEMA.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[patch] coverage blocks applied (changed={changed})")


if __name__ == "__main__":
    main()
