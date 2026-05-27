"""Audit V3 prospectus fact extraction quality without model calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ipo_portal.filing_processor import assess_extraction_quality


DEFAULT_SITE_ROOT = PROJECT_ROOT / "data" / "ipo_watch_v3"
DEFAULT_REPORT = PROJECT_ROOT / "data" / "reports" / "prospectus_facts_quality.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit citation-verified prospectus_facts.json quality.")
    parser.add_argument("--site-root", type=Path, default=DEFAULT_SITE_ROOT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--gate", action="store_true", help="Exit non-zero when any extraction quality fails.")
    parser.add_argument("--strict", action="store_true", help="With --gate, also fail review-state extractions.")
    parser.add_argument("--include-placeholders", action="store_true", help="Also audit no-document/not-extracted placeholders.")
    args = parser.parse_args()

    rows = []
    for path in sorted((args.site_root / "issues").glob("*/prospectus_facts.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not args.include_placeholders and not (doc.get("deepseek") or {}).get("used"):
            continue
        quality = doc.get("quality") or assess_extraction_quality(doc)
        rows.append(
            {
                "slug": doc.get("slug") or path.parent.name,
                "status": doc.get("extraction_status"),
                "quality_state": quality.get("state"),
                "verified_fact_count": quality.get("verified_fact_count"),
                "checked_count": quality.get("checked_count"),
                "redaction_rate": quality.get("redaction_rate"),
                "repair_rate": quality.get("repair_rate"),
                "missing_sections": quality.get("missing_sections") or [],
                "warnings": quality.get("warnings") or [],
                "failures": quality.get("failures") or [],
                "path": str(path),
            }
        )

    counts = {
        "total": len(rows),
        "pass": sum(1 for row in rows if row["quality_state"] == "pass"),
        "review": sum(1 for row in rows if row["quality_state"] == "review"),
        "fail": sum(1 for row in rows if row["quality_state"] == "fail"),
    }
    report = {
        "site_root": str(args.site_root),
        "counts": counts,
        "rows": rows,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"counts": counts, "report": str(args.report)}, ensure_ascii=False, sort_keys=True))

    if args.gate and counts["fail"]:
        return 1
    if args.gate and args.strict and (counts["fail"] or counts["review"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
