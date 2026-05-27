# Handoff Prompt For New Chat

Use this prompt verbatim in a new chat.

```txt
You are Codex working in:

/home/bhuvanesh.r/Documents/Bhuvan projects/IPO

Mission: continue IPO Watch V3. Read docs/V3_CURRENT_STATUS.md first, then continue from the current repo state.

Important current facts:

- Canonical V3 dataset root is now data/ipo_watch_v3.
- data/site_v3 is only a compatibility symlink to data/ipo_watch_v3.
- The Astro site loader defaults to data/ipo_watch_v3 through web/src/lib/ipodata.ts.
- Use .venv/bin/python unless a command specifically requires system Python.
- Never print .env or secrets.
- DeepSeek/OpenRouter usage is cached/logged; wrong data is worse than missing data.

Current V3 dataset:

- data/ipo_watch_v3 exists and is self-contained.
- Manifest counts: 5,196 public issues, 4,355 companies, 214 trajectories, 2,486 performance rows, 1,891 quarantined.
- scripts/audit_v3_quality.py --site-root data/ipo_watch_v3 --gate currently passes.
- scripts/audit_prospectus_facts.py --site-root data/ipo_watch_v3 --gate currently passes with 3 extracted filings: 2 pass, 1 review.

Recent important work:

- V3 moved from data/site_v3 to data/ipo_watch_v3, with compatibility symlink retained.
- Filing extraction pipeline exists in ipo_portal/filing_processor.py.
- It supports pdftotext and LiteParse local extraction.
- It uses DeepSeek for JSON extraction, page markers for source_page accuracy, citation verification, primitive-fact redaction, and pass/review/fail quality scoring.
- CLI: .venv/bin/python -m ipo_portal.orchestrator process-filings-v3
- Quality gates: --quality-gate and --strict-quality-gate.
- No-token prospectus audit: scripts/audit_prospectus_facts.py.
- Sample rich Markdown page: docs/samples/a-g-universal-drhp-sample.md.

Files to inspect first:

1. docs/V3_CURRENT_STATUS.md
2. docs/V3_CONTRACT.md
3. docs/DEEPSEEK_FILING_PROCESSOR.md
4. ipo_portal/filing_processor.py
5. ipo_portal/site_v3/export.py
6. web/src/lib/ipodata.ts
7. scripts/audit_v3_quality.py
8. scripts/audit_prospectus_facts.py

Suggested next task:

Build the V3 rich prospectus rendering path in the Astro site:

- Add a site adapter that reads issues/<slug>/prospectus_facts.json from data/ipo_watch_v3.
- Render only pass-state facts by default; label review-state facts clearly if included.
- Add sections for business snapshot, risks, financial highlights, industry/macro context, offer/advisors, valuation/peers, governance, and citations.
- Use docs/samples/a-g-universal-drhp-sample.md as the content target.
- Keep all facts citation-backed; do not invent missing fields.
- Run:
  python3 -m pytest -q --ignore=tests/test_kite_auth.py
  .venv/bin/python scripts/audit_v3_quality.py --site-root data/ipo_watch_v3 --gate
  .venv/bin/python scripts/audit_prospectus_facts.py --site-root data/ipo_watch_v3 --gate
  cd web && npm run build

Alternative next task:

Scale filing extraction in controlled batches:

.venv/bin/python -m ipo_portal.orchestrator process-filings-v3 \
  --limit 10 \
  --provider deepseek \
  --text-extractor liteparse \
  --quality-gate

Then audit:

.venv/bin/python scripts/audit_prospectus_facts.py --site-root data/ipo_watch_v3 --gate

If many filings go to review/fail, improve section aliases and prompt/schema before scaling further.

Known caveats:

- Only 3 real DeepSeek prospectus extractions are currently present.
- 20-microns-nano-minerals-f0cd70 is review because valuation_and_peers is missing and rates are elevated.
- Full acceptance suite has not been rerun after the folder rename.
- Running export-v3 may rebuild data/ipo_watch_v3; verify it preserves clean extracted prospectus_facts.json before treating it as safe.
- OpenRouter direct PDF tests were not production-ready; DeepSeek + LiteParse is the current preferred path.
```
