from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ipo_portal.site_v3 import export_v3
from ipo_portal.site_v3.export import _source_freshness


class SiteV3ExportTests(unittest.TestCase):
    def test_export_materializes_self_contained_v3_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "site_v2"
            out = root / "site_v3"
            schemas = root / "schemas"
            (source / "issues" / "by-slug").mkdir(parents=True)
            (source / "issues" / "example-ipo").mkdir(parents=True)
            (source / "companies" / "by-slug").mkdir(parents=True)
            (source / "trajectories").mkdir(parents=True)
            schemas.mkdir()

            self._write_json(
                source / "manifest.json",
                {
                    "$schema": "https://ipo-watch.local/schema/v2/manifest.schema.json",
                    "dataset_version": "v2026.05.24-0653",
                    "generated_at": "2026-05-24T06:53:54+00:00",
                    "issues_total": 1,
                    "issues_published": 1,
                    "companies_total": 1,
                    "trajectories_total": 1,
                },
            )
            self._write_json(
                source / "issues" / "index.json",
                {"items": [{"slug": "example-ipo", "url_path": "/ipo/example-ipo/"}], "count": 1},
            )
            self._write_json(
                source / "issues" / "by-slug" / "example-ipo.json",
                {
                    "$schema": "https://ipo-watch.local/schema/v2/issue.schema.json",
                    "slug": "example-ipo",
                    "url_path": "/ipo/example-ipo/",
                    "identity": {"company_name": "Example Limited"},
                },
            )
            self._write_json(
                source / "issues" / "example-ipo" / "prospectus.json",
                {"$schema": "https://ipo-watch.local/schema/v2/prospectus.schema.json", "slug": "example-ipo"},
            )
            self._write_json(
                source / "companies" / "by-slug" / "example-limited.json",
                {"slug": "example-limited", "url_path": "/company/example-limited/"},
            )
            self._write_json(source / "trajectories" / "example-ipo.json", {"slug": "example-ipo"})
            self._write_json(schemas / "issue.schema.json", {"$id": "https://ipo-watch.local/schema/v2/issue.schema.json"})

            stats = export_v3(source_root=source, out_root=out, schema_root=schemas)

            self.assertEqual(stats.issues, 1)
            self.assertEqual(stats.companies, 1)
            self.assertEqual(stats.trajectories, 1)
            self.assertEqual(stats.prospectuses, 1)

            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            issue = json.loads((out / "issues" / "by-slug" / "example-ipo.json").read_text(encoding="utf-8"))
            index = json.loads((out / "issues" / "index.json").read_text(encoding="utf-8"))
            contract = json.loads((out / "_meta" / "contract.json").read_text(encoding="utf-8"))

            self.assertEqual(manifest["schema_version"], "3.0.0")
            self.assertEqual(manifest["dataset_version"], "v3.2026.05.24-0653")
            self.assertFalse(manifest["degraded"])
            self.assertEqual(manifest["stale_sources"], [])
            self.assertTrue(manifest["site_contract"]["self_contained"])
            self.assertEqual(issue["url_path"], "/ipos/example-ipo/")
            self.assertTrue(issue["prospectus_available"])
            self.assertEqual(index["items"][0]["url_path"], "/ipos/example-ipo/")
            self.assertTrue(contract["self_contained"])
            self.assertTrue((out / "_meta" / "schemas" / "issue.schema.json").exists())

    def test_source_freshness_marks_required_sources_stale(self) -> None:
        doc = _source_freshness(
            [
                {"meta": {"source": "nse", "endpoint": "ipo_current_issue", "fetched_at": "2020-01-01T00:00:00+00:00"}},
                {"meta": {"source": "bse", "endpoint": "public_issue", "fetched_at": "2020-01-01T00:00:00+00:00"}},
            ]
        )

        self.assertTrue(doc["degraded"])
        self.assertIn("sebi", {row["source"] for row in doc["stale_sources"]})
        self.assertIn("market_data", {row["source"] for row in doc["stale_sources"]})

    @staticmethod
    def _write_json(path: Path, doc: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
