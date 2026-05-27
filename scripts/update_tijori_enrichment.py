from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ipo_portal.tijori import (  # noqa: E402
    fetch_tijori_ipo_feed,
    write_sector_map_from_tijori,
    write_tijori_enrichment,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Tijori Kite IPO screener enrichment data.")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "data" / "derived" / "tijori_ipo_enrichment.json")
    parser.add_argument("--sector-map", type=Path, default=PROJECT_ROOT / "data" / "derived" / "sector_map.json")
    parser.add_argument("--skip-sector-map", action="store_true")
    args = parser.parse_args()

    rows = fetch_tijori_ipo_feed()
    enrichment = write_tijori_enrichment(rows, args.out)
    sector_count = 0
    if not args.skip_sector_map:
        sector_count = len(write_sector_map_from_tijori(enrichment, args.sector_map))
    print(
        json.dumps(
            {
                "rows": enrichment["stats"]["rows"],
                "with_isin": enrichment["stats"]["with_isin"],
                "with_financials": enrichment["stats"]["with_financials"],
                "enrichment_path": str(args.out),
                "sector_map_path": None if args.skip_sector_map else str(args.sector_map),
                "sector_map_entries": sector_count,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
