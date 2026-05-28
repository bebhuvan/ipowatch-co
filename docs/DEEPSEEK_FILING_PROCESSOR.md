# DeepSeek Filing Processor

Goal: extract SEO-grade, citation-verified facts from primary filing PDFs without publishing hallucinated claims.

## Why This Exists

Most IPO pages only mirror dates, prices, GMP-style snippets, or shallow summaries. Primary filings contain richer information: business model, industry overview, macro story, revenue mix, risk factors, financial trends, peer comparison, litigation, related-party transactions, offer objects, debt terms, and red flags. V3 stores those facts in `data/ipo_watch_v3/issues/<slug>/prospectus_facts.json`.

## DeepSeek API Contract

The harness uses DeepSeek's OpenAI-compatible chat endpoint at `https://api.deepseek.com`, JSON mode via `response_format={"type":"json_object"}`, and the current default model `deepseek-v4-flash`. DeepSeek's docs note that JSON mode requires `response_format`, the word `json` in the prompt, an example/shape, and enough `max_tokens` to avoid truncation. Usage and cache-hit fields are logged by `ipo_portal.deepseek`.

## Safety Design

- PDF is downloaded and cached under `data/cache/primary_filings/`.
- ZIP filings are unwrapped to the largest PDF.
- The model input mode is explicit:
  - `--input-mode pdf-url` sends a direct public PDF URL to OpenRouter. This is the preferred path for SEBI DRHP/RHP URLs and PDF-capable models such as Gemini or Qwen VL.
  - `--input-mode pdf` sends a direct PDF URL when the URL is visibly a PDF, otherwise sends the cached/unwrapped local PDF as base64.
  - `--input-mode pdf-base64` always sends the cached local PDF as a base64 PDF file part. This is useful for ZIP-wrapped NSE/BSE documents.
  - `--input-mode text` uses `pdftotext -layout` slices. This is the cheap fallback and remains useful for deterministic regression tests.
- `--text-extractor pdftotext|liteparse` controls the local text layer used for text-mode prompts and citation verification. `pdftotext` remains the default. `liteparse` is optional, page-aware, cached under `data/cache/pdf_text/liteparse/`, and can be installed with `requirements-extractors.txt`.
- For OpenRouter PDF input, `--pdf-engine native` is the default. `cloudflare-ai`, `mistral-ocr`, and `none` are available for experiments.
- Gemini Flash remains the primary multimodal/PDF fallback for broad PDF
  understanding. MiMo is experimental and should be used as a JPEG-only visual
  fallback for table-heavy pages where local text extraction mangles rows.
- The document is sliced by primary sections: business, industry, financials, risks, offer, valuation/peers, governance.
- DeepSeek must emit strict JSON.
- Every scalar fact must carry `value`, `raw_excerpt`, `source_page`, `source_section`, and `confidence`.
- `raw_excerpt` must be exact contiguous text from the filing.
- After model output, every non-null fact is independently verified against the cited PDF page using the selected local text extractor. Rasterized page OCR can still be added as another verifier without changing the published V3 contract.
- If the quote appears on a different page, `source_page` is repaired.
- If the quote cannot be found, the fact is redacted to null.
- Costs and token usage are logged to `data/reports/deepseek_usage.jsonl` or `data/reports/openrouter_usage.jsonl`, depending on provider.

## Commands

List candidate primary filings:

```bash
.venv/bin/python -m ipo_portal.orchestrator process-filings-v3 --list --limit 20
```

Dry-run PDF download/text extraction without DeepSeek:

```bash
.venv/bin/python -m ipo_portal.orchestrator process-filings-v3 --slug a-g-universal-c13e20 --dry-run
```

Process one filing:

```bash
.venv/bin/python -m ipo_portal.orchestrator process-filings-v3 --slug a-g-universal-c13e20 --model deepseek-v4-flash
```

Process one filing using LiteParse as the local text/verifier layer:

```bash
.venv/bin/python -m ipo_portal.orchestrator process-filings-v3 \
  --slug a-g-universal-c13e20 \
  --model deepseek-v4-flash \
  --text-extractor liteparse
```

Process a direct SEBI PDF with Gemini/Qwen VL via OpenRouter:

```bash
.venv/bin/python -m ipo_portal.orchestrator process-filings-v3 \
  --provider openrouter \
  --model google/gemini-2.5-flash \
  --input-mode pdf-url \
  --url 'https://www.sebi.gov.in/filings/public-issues/example-drhp.pdf' \
  --slug example-issuer \
  --document-type DRHP
```

Benchmark PDF-capable models without writing public facts:

```bash
.venv/bin/python -m ipo_portal.orchestrator process-filings-v3 \
  --provider openrouter \
  --slug a-g-universal-c13e20 \
  --input-mode pdf \
  --benchmark-section business \
  --benchmark-models qwen/qwen3-vl-8b-instruct,qwen/qwen3-vl-30b-a3b-instruct,google/gemini-2.5-flash
```

Benchmark MiMo JPEG extraction without writing public facts:

```bash
python3 scripts/test_mimo_drhp_images.py \
  --slug a-g-universal-c13e20 \
  --section financials \
  --pages 1 \
  --dpi 180 \
  --max-tokens 8192
```

The MiMo harness renders selected PDF pages with `pdftoppm -jpeg`, sends only
JPEG data URLs to Xiaomi MiMo, and writes a diagnostic report at
`data/reports/mimo_drhp_image_test.json`. Keep this as a benchmark/fallback path
until visual extraction has independent verification comparable to the text
citation verifier.

Compare local PDF extraction backends before choosing the verifier input:

```bash
.venv/bin/python scripts/compare_pdf_extractors.py \
  --slug manipal-health \
  --url 'https://www.manipalhospitals.com/assets/pdf/drhp-manipal-hospitals.pdf' \
  --pages 5-50
```

The comparison writes raw outputs under `data/cache/pdf_extraction/<slug>/` and a report at `data/reports/pdf_extractor_benchmark.json`.

Process a small batch:

```bash
.venv/bin/python -m ipo_portal.orchestrator process-filings-v3 \
  --limit 5 \
  --model deepseek-v4-flash \
  --text-extractor liteparse \
  --quality-gate
```

Batch discovery skips filings that already have a current pass/review
`prospectus_facts.json` for the same document URL. Use `--force` only when you
intend to reprocess an existing extraction:

```bash
.venv/bin/python -m ipo_portal.orchestrator process-filings --force --slug a-g-universal-c13e20
```

Short alias:

```bash
.venv/bin/python -m ipo_portal.orchestrator process-filings \
  --limit 5 \
  --model deepseek-v4-flash \
  --text-extractor liteparse \
  --quality-gate
```

Audit already-extracted prospectus facts without making model calls:

```bash
.venv/bin/python scripts/audit_prospectus_facts.py --site-root data/ipo_watch_v3 --gate
```

Manual URL:

```bash
.venv/bin/python -m ipo_portal.orchestrator process-filings-v3 \
  --slug example-issuer \
  --url 'https://example.com/rhp.pdf' \
  --document-type RHP
```

## Output

Successful extraction writes:

```txt
data/ipo_watch_v3/issues/<slug>/prospectus_facts.json
```

The file includes document hash, PDF page count, DeepSeek call telemetry, section facts, and citation validation results. `export-v3` preserves clean extracted facts across rebuilds.

`export-v3` preserves model-produced `pass` and `review` extraction outputs
across dataset rebuilds. Failed extractions and placeholder files are not
reattached as publishable facts.

The Astro site reads this file directly from
`data/ipo_watch_v3/issues/<slug>/prospectus_facts.json` and renders only
non-null citation-backed facts.

## Caveats

- The harness is conservative; it will omit or redact useful claims if exact citation verification fails.
- `prospectus_facts.json` is a verified fact layer, not article copy. The site
  should render it as narrative sections, compact tables, charts, and quiet
  source notes. Do not expose one fact row per citation as the final article
  experience.
- Large PDFs can still be expensive. Start with `--limit 1` and inspect cost logs.
- Parser gaps in source coverage are separate from filing extraction and should be closed independently.
