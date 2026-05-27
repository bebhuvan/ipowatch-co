# Academy source harvest — process and schema

The Academy pipeline (`ipo_portal.orchestrator.academy`) refuses to
draft anything that is not grounded in a harvested source corpus.
This document defines how sources are gathered, how they are stored,
and what the brief looks like.

## Where things live

```
data/academy/
├── seed_lists/                   # planned URLs per article, human-authored YAML
│   └── <slug>.yaml
├── sources/                      # harvested SourceDoc JSON files, one per source_id
│   ├── sebi-icdr-2018-sched-xiii.json
│   ├── sebi-circular-2012-allotment-method.json
│   └── ...
└── runs/                         # per-article pipeline artifacts
    └── <slug>/
        ├── brief.json
        ├── outline.json
        ├── draft.md
        ├── fact_check.json
        ├── edited.md
        └── visual_brief.json
```

Sources are global. One source can support many articles. The brief
declares which subset of `source_ids` is in scope for a given article.

## The SourceDoc schema

Every file in `data/academy/sources/<source_id>.json` matches this
shape (also captured as a frozen dataclass in
`ipo_portal/orchestrator/academy.py`):

```json
{
  "source_id": "sebi-icdr-2018-sched-xiii",
  "title": "Schedule XIII: Allocation in net offer to public — SEBI ICDR Regulations, 2018",
  "url": "https://www.sebi.gov.in/legal/regulations/...",
  "publisher": "Securities and Exchange Board of India",
  "published_at": "2018-09-11",
  "fetched_at": "2026-05-23T10:14:00+00:00",
  "source_tier": "primary",
  "document_kind": "regulation",
  "excerpts": [
    "Verbatim excerpt 1 from the document, kept word-for-word as it appears...",
    "Verbatim excerpt 2..."
  ],
  "notes": "Optional human note: what part of the document this is, why it matters, any caveats."
}
```

### Field rules

* `source_id` is a stable kebab-case slug. Convention:
  `<publisher>-<doctype>-<year>-<short-handle>`.
  Examples: `sebi-icdr-2018-sched-xiii`,
  `nse-rulebook-ipo-allotment-2023`,
  `aibi-handbook-2022-anchor-investors`.
  The slug is the JSON filename without `.json`.

* `source_tier` must be one of:
  * `primary`     — Regulator (SEBI, RBI, MCA), exchange rulebook (NSE,
                    BSE), industry self-regulator handbook (AIBI). Also
                    the regulation/circular/master direction itself.
  * `secondary`   — Reputable financial press for *specific factual
                    events*: pricing of an IPO, exact subscription
                    multiples, named anchor allocations. *Reuters,
                    Bloomberg, Mint, BusinessLine, BQ Prime,
                    Economic Times reports specifically; not Moneycontrol
                    listicles or Chittorgarh aggregations.*
  * `enrichment`  — Academic papers, books by recognized authors, RBI
                    bulletins, working papers, court orders.

* `document_kind` must be one of:
  `regulation | circular | rulebook | master-direction | handbook |
  report | filing | news | book | working-paper | court-order`.

* `excerpts` are *verbatim*. The model only sees these strings; it
  does not see the rest of the document. We accept that this is more
  manual work; it is the cost of removing the hallucination surface.
  An excerpt should be a complete sentence or block. Trim leading/
  trailing whitespace. Do not edit for clarity, do not paraphrase.

* `published_at` is the document's own publication or amendment date.
  May be null only when the document does not bear a date (rare).

* `fetched_at` is when *we* pulled the page. ISO-8601 UTC with offset.

### What counts as a source

Yes:

* SEBI Acts, Rules, Regulations (ICDR especially), Master Circulars.
* SEBI orders and adjudications when the article narrates a specific
  enforcement event.
* RBI Master Directions and circulars for FPI, ECB, share-transfer
  rules.
* MCA Companies Act sections.
* NSE and BSE rulebooks, circulars, FAQs.
* AIBI handbook.
* Court judgements (SAT, Supreme Court, High Courts).
* Reuters, Bloomberg, Mint, BusinessLine, BQ Prime, Economic Times
  reports for specific events.
* Books by Tamal Bandyopadhyay, James Crabtree, T. N. Ninan, etc.
* RBI working papers, SEBI Annual Reports.

No:

* Investopedia, ClearTax, Groww, Zerodha Varsity, Moneycontrol
  glossary, Chittorgarh, brokerage marketing blogs, NSE Academy
  marketing pages, IIM blog posts, Medium articles, LinkedIn posts.
* Wikipedia — but its footnotes pointing to primary sources are a
  legitimate starting point; we then cite the primary source.
* Anything paywalled where we cannot quote the excerpt back.

If a source is borderline, write a `notes` field explaining the call.

## The harvest process

For each `<slug>` we want to write:

1. Author a `seed_lists/<slug>.yaml` with the URLs and intended
   `source_id`s. This is the planning step; the user reviews it
   before any fetching happens.

2. For each URL in the seed list, fetch the page. Convert the
   relevant passages to UTF-8 plain text. Build the SourceDoc JSON.
   Write to `data/academy/sources/<source_id>.json`.

3. If a source already exists from a previous article and is still
   accurate, do not re-fetch. Reuse it.

4. After harvesting, hand-author `runs/<slug>/brief.json` (see below)
   referencing the harvested `source_id`s.

5. Run the pipeline:
   `python -m ipo_portal.orchestrator academy --slug <slug>`.

## The seed list format

`data/academy/seed_lists/<slug>.yaml`:

```yaml
slug: how-allotment-works
section: mechanics
working_title: How IPO allotment actually works
audience_one_line: >
  An Indian retail or HNI applicant who has bid in an IPO and wants
  to understand exactly how the shares get distributed.

sources:
  - source_id: sebi-icdr-2018-sched-xiii
    url: https://www.sebi.gov.in/legal/regulations/...
    publisher: SEBI
    document_kind: regulation
    source_tier: primary
    intended_excerpts: >
      Sections defining the proportions for QIB, NII, retail, employee,
      and shareholder reservation; the formula for lottery vs proportional.

  - source_id: sebi-circular-2012-allotment-method
    url: https://www.sebi.gov.in/legal/circulars/...
    publisher: SEBI
    document_kind: circular
    source_tier: primary
    intended_excerpts: >
      The 2012 reform replacing proportional with one-lot lottery for
      oversubscribed retail.

  # ... etc
```

The seed list is human-reviewed before any WebFetch hits a server.
This is the budget gate.

## The brief schema

`data/academy/runs/<slug>/brief.json`:

```json
{
  "slug": "how-allotment-works",
  "section": "mechanics",
  "working_title": "How IPO allotment actually works",
  "audience_one_line": "An Indian retail or HNI applicant who has bid in an IPO and wants to understand exactly how the shares get distributed.",
  "must_answer": [
    "How are total shares carved between QIB, NII (small and big), retail, employee, shareholder?",
    "What is the lottery method and when did it replace proportional allotment for retail?",
    "How does the 1-lot floor and the maximum-applicants rule work?",
    "How is sHNI (₹2L–10L) and bHNI (>₹10L) reservation actually split?",
    "What happens when a category is undersubscribed?",
    "How does anchor allocation interact with the QIB book?"
  ],
  "must_not_say": [
    "How to predict allotment chances (we don't speculate).",
    "Anything implying allotment is influenceable (it is not, per SEBI rules)."
  ],
  "angle": "Indian retail allotment is a lottery, not a market. Explain the mechanic, not the strategy.",
  "length_target_words": 1800,
  "source_ids": [
    "sebi-icdr-2018-sched-xiii",
    "sebi-circular-2012-allotment-method",
    "nse-allotment-faq-2024",
    "aibi-handbook-anchor",
    "sebi-master-circular-issue-2024"
  ],
  "notes": "Anchor article; sets the editorial bar."
}
```
