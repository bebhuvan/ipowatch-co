# Codex Audit Brief — Verify the IPO Watch data pipeline from scratch

> **Paste everything below the line into a fresh Codex (or any capable agent)
> session, with this repository as the working directory.** It is written to be
> self-contained. The goal is an independent, adversarial, ground-up
> verification that the data is pristine and the pipeline is clean and
> replicable — not a friendly skim.

---

## YOUR MISSION

You are auditing the data pipeline of **IPO Watch**, a canonical database of
Indian primary-market issuances (IPO/FPO/OFS/rights/buyback/NCD/REIT/InvIT/
SGB/IPP) scraped from NSE, BSE, SEBI, Zerodha Kite, and aggregators, normalized
into a static JSON tree (`data/site_v2/`) that powers a website.

The owner wants three guarantees and is paying you to be skeptical about all
three:

1. **Pristine data** — every published value is correct, traceable to a primary
   source, and in the right unit. No silent corruption, no fabrication, no
   mislabeling.
2. **Clean pipeline** — deterministic, idempotent, well-separated, no dead code
   masquerading as live, no hidden state.
3. **Replicable** — someone can reproduce the entire dataset from raw sources
   (or re-fetch from the exchanges) and get the same result, with no
   undocumented manual steps.

**Adversarial mindset — read this twice:**
- **Trust nothing you are told, including the status doc.** A prior agent wrote
  `docs/PIPELINE_STATUS.md` claiming everything is fixed and clean. Treat every
  claim in it as a hypothesis to falsify, not a fact. Where a claim says "X is
  fixed," your job is to reproduce X and confirm — or find the case it missed.
- **Verify against primary sources.** The exchanges (NSE/BSE) and the actual
  RHP PDFs are ground truth. The repo's `data/` is a derived artifact and could
  be wrong. When in doubt, **re-fetch the raw data and re-derive** — you are
  explicitly authorized to fetch everything from scratch if that is what it
  takes to be sure.
- **Granularity is the point.** Do not sample three records and declare
  victory. Sweep whole classes. Open individual records. Compare raw → parsed →
  canonical for specific issues, byte by byte where it matters.
- **Wrong is worse than missing.** A null is acceptable; a confidently-wrong
  number is a defect. Flag any value that looks plausible but is unverified.

---

## GROUND TRUTH MAP (orient first, then verify)

Read these before touching anything, in order:
1. `docs/PIPELINE_STATUS.md` — the claimed current state (your falsification target).
2. `docs/ASTRO_HANDOFF.md` — the data contract (record shape, units, prospectus).
3. `docs/data/SCHEMA_GUIDE.md`, `docs/data/DATASET.md`, `docs/data/SOURCES.md`.
4. `docs/data/EDGE_CASES.md`, `docs/data/FUTURE_PROOFING.md`, `docs/data/COVERAGE_AUDIT.md`.
5. `docs/data/SOURCE_PRECEDENCE.yaml`, `docs/data/DEDUP_RULES.yaml`.
6. `docs/schema/v2/*.schema.json` (issue, company, trajectory, prospectus, index).
7. The code: `ipo_portal/normalize_v2/pipeline.py` (the heart), `identity.py`,
   `precedence.py`, every file in `parsers/`, `validation_v2/`,
   `scripts/extract_rich_rhp.py`, `scripts/audit_v2_quality.py`.

**Environment:** Python 3.12, virtualenv at `.venv/`. Use `.venv/bin/python`.
Tests: `python3 -m pytest -q --ignore=tests/test_kite_auth.py` (system pytest;
`.venv` lacks pytest, system python lacks `pyotp`). Secrets in `.env` (Kite,
DeepSeek) — never print them, never paste them anywhere.

**Canonical units (memorize — the #1 source of bugs):** money = integer
**paise**; percent = integer **basis points**; subscription multiple =
**decimal-string** (`"26.7600"`); dates = ISO 8601; timezone IST; currency INR.

---

## THE AUDIT — work through every phase, produce findings as you go

For each phase, record findings in `docs/AUDIT_FINDINGS.md` (create it) with:
`[severity] area — what you checked — what you found — evidence (commands/paths)
— recommended fix`. Severities: 🔴 critical (corruption / wrong data / breaks
replicability), 🟡 should-fix, 🟢 nit/observation, ✅ verified-clean.

### Phase 0 — Reproduce the build (is it even deterministic?)
- [ ] Run `.venv/bin/python -m ipo_portal.orchestrator normalize`. Does it
      complete? Record issue/company/trajectory counts from `manifest.json`.
- [ ] Confirm **manifest == on-disk file counts** for all three trees
      (issues/by-slug, companies/by-slug, trajectories). A mismatch = silent
      record loss or stale orphans. (The status doc claims parity — verify.)
- [ ] Run normalize **twice** and diff `data/site_v2`. It must be byte-identical
      (deterministic). Any nondeterminism (dict ordering, timestamps leaking
      into content, race) is a 🔴 — it breaks replicability.
- [ ] Run `python3 -m pytest -q --ignore=tests/test_kite_auth.py`. All pass?
      Read the tests — do they actually assert the invariants, or are they
      shallow? Note weak/missing coverage.

### Phase 1 — Canonical conventions (units, identity, enums)
- [ ] Sweep ALL `*_paise` fields: any non-integer, negative (where impossible),
      or suspiciously small (e.g. a band of 9500 paise = ₹95 is fine; 95 paise
      = ₹0.95 is almost certainly a unit error). Quantify.
- [ ] Sweep `*_bps`: any gain < −10000 (worse than −100% — mathematically
      impossible). The status doc claims these were fixed by deriving gains from
      prices — re-derive a sample yourself and confirm gain ⇔ price consistency.
- [ ] Sweep `*_x`: all decimal-strings, none negative, none absurd.
- [ ] Validate every issue record against `docs/schema/v2/issue.schema.json`
      (draft 2020-12) independently of the pipeline's own validator. Any failures?
- [ ] Enums: every `issue_type`/`status`/`board_type` in the closed set.
- [ ] Slugs: unique, match `^[a-z0-9]+(-[a-z0-9]+)+$`, equal `identity.slug`.
      Confirm no two distinct records share a by-slug filename.
- [ ] Identity: any company_name still carrying the `^\d+\+` artifact, HTML,
      emails, or addresses? Any ISIN that fails the structural ISIN regex?

### Phase 2 — Consolidation correctness (the hardest part)
This is where the worst bugs hide (duplicates, fused issues, corrupted prices).
- [ ] **Duplicates:** group by `(normalize_name, issue_type, listing_year)`.
      Any group with >1 record where the year is known is a true duplicate — the
      status doc claims **zero**. Verify, and also probe ISIN-level dups.
- [ ] **Over-merge:** find records whose `sources[]` span implausibly different
      dates or whose `field_provenance` mixes a 2003 listing with a 2022 offer
      (the "Ruchi Soya" failure mode). Are distinct issues of one company kept
      distinct? Pick a serial issuer (e.g. an NBFC with many NCD tranches, or a
      company that did IPO then FPO) and confirm each issuance is its own record
      with the right ISIN/year.
- [ ] **Under-merge:** find the same issue split across two records (e.g. one
      from the IPO list, one from the per-issue detail) that *should* be one.
      Spot-check current IPOs: does the NSE list record and the NSE
      `issue_detail` record end up as ONE record?
- [ ] **The ipo_no hard boundary:** confirm two distinct BSE issues sharing a
      company name but different `bse_ipo_no` are NOT fused (this previously
      corrupted price bands — e.g. Adani Wilmar's IPO vs a later action).
- [ ] **Precedence:** for a field contributed by multiple sources, confirm the
      winner matches `SOURCE_PRECEDENCE.yaml` and `field_provenance` records the
      decision honestly.

### Phase 3 — Source coverage & fidelity (re-fetch and compare)
Pick **~15 "golden" issues** spanning every type and era: a recent main-board
IPO, an SME IPO (NSE Emerge AND BSE SME), an FPO, an OFS, a rights issue, a
buyback, an NCD/debt public issue, a REIT, an InvIT, an IPP/tender, an SGB, a
withdrawn issue, a very old (pre-2010) issue, and a currently-open issue.
- [ ] For each: open the canonical record, then open the underlying raw
      snapshot(s) in `data/raw/`, then **fetch the live page from NSE/BSE** and
      compare. Do band, dates, issue size, subscription, lead managers,
      registrar, listing gain all match the exchange? Note every discrepancy.
- [ ] Confirm both subscription books are present where expected
      (`subscription.consolidated` AND `subscription.by_exchange.{nse,bse}`) and
      that category keys + times are faithful to the exchange bid book.
- [ ] Re-run `ipo_portal fetch --source all` (or targeted endpoints) and confirm
      the parsers still handle live responses — upstream JSON shapes drift.
      Check `orchestrator drift` output and characterize every "removed"/
      "type_changed" event (the status doc says ~1,450 are benign catalog
      staleness — verify none hide a genuinely broken parser).
- [ ] Coverage gaps: is any primary issuance type or exchange feed unparsed?
      Cross-check `sources.py` endpoints against `parsers/` registrations.

### Phase 4 — Data-quality audit (run it, then go beyond it)
- [ ] Run `.venv/bin/python scripts/audit_v2_quality.py`. Characterize EVERY
      finding class — for each, is it a real defect or a legitimate source
      characteristic? Don't accept the status doc's "all explained" at face
      value; open the flagged records.
- [ ] Run with `--gate` and confirm it exits 0 on the current (clean) tree, and
      that it would exit non-zero on a deliberately corrupted tree (test it:
      delete a by-slug file without updating the manifest, run `--gate`, confirm
      it fails, then restore).
- [ ] Look for classes the audit does NOT check: cross-field consistency (e.g.
      issue_price within band; listing_date ≥ close_date ≥ open_date;
      status ⇔ dates), orphaned trajectory/prospectus files, company-index ⇔
      issue back-references, alias collisions.

### Phase 5 — RHP extraction integrity (the differentiator)
- [ ] Validate every `issues/<slug>/prospectus.json` against
      `docs/schema/v2/prospectus.schema.json`.
- [ ] For one prospectus, **open the actual RHP PDF** and verify several
      `raw_excerpt`/`source_page` citations are real (the text exists on that
      page) and the `value` faithfully reflects it. **Any fabricated citation or
      hallucinated value is a 🔴** — provenance is the whole product.
- [ ] Confirm the NSE zipped-RHP path works: take an NSE `documents.rhp_url`
      (a `.zip`), run the extractor's download path, confirm it unwraps to the
      RHP PDF and `pdftotext` reads it.
- [ ] Confirm go-forward discipline: no historical backfill happened; only
      current/recent issues have prospectus.json; re-running enrich is idempotent
      (skips already-extracted) and does not re-spend DeepSeek.

### Phase 6 — Replicability & cleanliness (the part most likely to be weak)
- [ ] **Version control:** confirm whether this is a git repo. (The status doc
      says it is NOT.) If not, this is a 🔴 for replicability — there is no
      history, no rollback, no provenance for the code or data. Recommend
      `git init`, a `.gitignore` strategy for the 327 MB `data/raw`, and what
      to track.
- [ ] **Clean-room rebuild:** from `data/raw` alone (no other state), can you
      reproduce `data/site_v2` exactly? Identify every input the pipeline reads
      (raw snapshots, `data/derived/sector_map.json`, `.env`, caches) and
      whether each is reproducible or a hidden manual artifact.
- [ ] **Idempotency / hash-gating:** confirm re-running writes nothing when
      inputs are unchanged, and that `_prune_stale` removes orphans correctly.
- [ ] **Caches:** `data/cache/deepseek` and `data/cache/rhp_pdfs` make reruns
      free — but do they ever mask a stale/incorrect result? Confirm cache keys
      are content/inputs-derived, not time-derived.
- [ ] **Dead code:** is `orchestrator/rhp_enrich.py` actually unused (superseded
      by the script)? Are `normalize.py`/`site_builder.py` (v1) still needed?
      Flag anything that looks live but isn't (and vice versa).
- [ ] **Dependencies:** does `requirements.txt` cover everything the pipeline
      imports (including `pdftotext`/poppler as a system dep)? Try a fresh venv
      install and a build to be sure.

### Phase 7 — CI & operations
- [ ] Read `.github/workflows/refresh-data.yml` and `refresh-full.yml`. Confirm:
      they run the v2 pipeline, gate the commit on the audit, commit the right
      trees, pass secrets via `env:` (not interpolated into `run:`), and the two
      can't race (shared concurrency group). Any injection risk or unguarded
      step?
- [ ] Confirm the audit gate genuinely blocks a corrupt commit (Phase 4 test).
- [ ] Cost model: confirm DeepSeek is bounded (daily only, limit, idempotent).
- [ ] Confirm graceful degradation when secrets are absent (no crash, cycle
      continues, no partial/corrupt commit).

### Phase 8 — Regression-proofing
- [ ] Re-verify each of the 4 fixed regression classes in
      `PIPELINE_STATUS.md §9` by trying to reproduce the original failure on the
      current code. If you can still trigger any of them, that's a 🔴.
- [ ] Propose specific new tests/audit-checks for any gap you found that isn't
      currently guarded.

---

## DELIVERABLES

1. **`docs/AUDIT_FINDINGS.md`** — every finding with severity, evidence
   (exact commands/paths/line numbers), and a recommended fix. Lead with a
   one-paragraph verdict: is the data pristine? is the pipeline clean? is it
   replicable? — yes/no/with-caveats, each justified.
2. **A prioritized fix list** — what to fix before relying on this in
   production, in order.
3. For any 🔴 you can fix safely and verifiably, fix it, re-run the relevant
   validation, and note it. Do NOT make sweeping changes without showing the
   before/after evidence. Do NOT spend DeepSeek/Kite credits beyond what's
   needed for spot-checks, and confirm with the owner before any bulk re-fetch
   that costs money or hammers the exchanges.

## RED FLAGS (stop and dig if you see any)
- manifest count ≠ on-disk count (silent loss / orphans).
- A gain worse than −100%, a band where lower > upper (outside OFS/Buyback),
  issue_price outside the band, a price in the wrong unit.
- Two records that are obviously the same issue, or one record fusing two issues.
- A prospectus `raw_excerpt` that isn't actually on the cited page.
- Nondeterministic rebuild output.
- A "fixed" claim you can still reproduce as broken.
- Any value you cannot trace to a primary source.

Be exhaustive. The owner explicitly prefers a slow, thorough, re-fetch-and-
re-derive audit over a fast reassuring one.
