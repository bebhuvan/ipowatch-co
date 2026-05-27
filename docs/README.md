# IPO Watch docs

This directory is the canonical documentation for the IPO Watch
dataset and pipeline. Start here.

## For dataset consumers

If you're ingesting `data/site_v2/` records (or planning to), read in this
order:

1. **[`data/DATASET.md`](data/DATASET.md)** — what the dataset is,
   coverage, conventions, license.
2. **[`data/SCHEMA_GUIDE.md`](data/SCHEMA_GUIDE.md)** — per-field
   semantics, units, display rules.
3. **`schema/v2/`** — JSON Schemas (draft 2020-12). Every record
   carries a `$schema` URL pointing here.
4. **[`data/SOURCES.md`](data/SOURCES.md)** — upstream endpoints we
   scrape, refresh cadence, staleness tolerance.
5. **[`data/CHANGELOG.md`](data/CHANGELOG.md)** — schema version
   history.

If your consumer is an LLM agent: every record is self-describing
via the metadata envelope (`$schema`, `dataset`, `dataset_version`,
`generated_at`, `sources[]`, `field_provenance{}`, `data_quality`,
`freshness`, `license`). Read a single record in isolation and you have
the full context.

## For contributors

If you're adding sources, fields, validation rules, or normalizer
behavior:

1. **[`data/FUTURE_PROOFING.md`](data/FUTURE_PROOFING.md)** — binding
   policy. Schema evolution, validation tiers, drift detection,
   identifier policy, time/money/locale, tests, secrets.
2. **[`data/EDGE_CASES.md`](data/EDGE_CASES.md)** — every contamination
   pattern we defend against, with stable rule IDs
   (`E.<CATEGORY>.<NNN>`).
3. **[`data/SOURCE_PRECEDENCE.yaml`](data/SOURCE_PRECEDENCE.yaml)** —
   declarative rules for which source wins when two disagree.
4. **`decisions/`** — Architecture Decision Records. New significant
   choices get a new file using
   [`decisions/000-template.md`](decisions/000-template.md).
5. **`schema/raw_catalog/`** — per-endpoint field catalogues
   (DeepSeek-generated, human-reviewed) describing what every upstream
   API actually returns and what contamination it carries.

## For operators

* **[`OPERATIONS.md`](OPERATIONS.md)** — the playbook: commands, cron
  cadence, monitoring signals, break-glass procedures.
* **[`REFRESH_CYCLE.md`](REFRESH_CYCLE.md)** — hot vs full refresh,
  per-source freshness contract, cron setup.
* `data/reports/refresh_runs.jsonl` — per-run, per-step status + timing.
* `data/reports/upstream_drift.jsonl` — append-only drift log; watch it
  for upstream changes.
* `data/reports/deepseek_usage.jsonl` — orchestrator spend audit.

## For the website / external consumers

* **[`CONSUMER_GUIDE.md`](CONSUMER_GUIDE.md)** — how to read
  `data/site_v2/`, the metadata envelope, storage-unit conventions, and
  validation. Start here if you're building the Astro pages or ingesting
  the data elsewhere.
* **[`data/IPO_PAGE_SCHEMA.md`](data/IPO_PAGE_SCHEMA.md)** — the rich
  IPO-page data contract filled by RHP extraction.

## Layout

```
docs/
├── README.md                       ← you are here
├── data/
│   ├── DATASET.md                  — overview for consumers
│   ├── SCHEMA_GUIDE.md             — per-field semantics
│   ├── SOURCES.md                  — upstream endpoints + cadence
│   ├── SOURCE_PRECEDENCE.yaml      — source-disagreement rules
│   ├── EDGE_CASES.md               — contamination patterns
│   ├── FUTURE_PROOFING.md          — binding policy
│   └── CHANGELOG.md                — schema version history
├── decisions/                      — Architecture Decision Records
│   ├── 000-template.md
│   ├── 001-canonical-storage-units.md
│   └── 002-parallel-v2-rebuild.md
└── schema/
    ├── raw_catalog/                — per-endpoint shape + risks
    │   ├── index.json
    │   ├── nse/<endpoint>.json
    │   ├── bse/<endpoint>.json
    │   └── …
    └── v2/                         — canonical JSON Schemas
        ├── issue.schema.json       (Phase 2 output)
        ├── company.schema.json
        └── trajectory.schema.json
```
