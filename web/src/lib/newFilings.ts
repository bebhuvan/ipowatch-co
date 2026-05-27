import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { ROOT } from './ipodata';

export type NewFilingFact = {
  path: string;
  label?: string;
  value: string | number;
  raw_excerpt: string;
  source_page: number;
  confidence: 'high' | 'medium' | 'low' | string;
};

export type NewFilingSection = {
  title: string;
  facts: NewFilingFact[];
};

export type NewFilingTableRow = {
  label?: string;
  value?: string | number;
  facts?: NewFilingFact[];
  source_pages?: number[];
  cells?: {
    label: string;
    value?: string | number;
    fact?: NewFilingFact | null;
  }[];
};

export type NewFilingArticleBlock = {
  type: string;
  title: string;
  kicker?: string;
  dek?: string;
  paragraphs?: string[];
  facts?: NewFilingFact[];
  groups?: { label: string; items: NewFilingFact[] }[];
  columns?: string[];
  rows?: NewFilingTableRow[] | { risk?: NewFilingFact | null; why_it_matters?: NewFilingFact | null }[];
  red_flags?: NewFilingFact[];
  risk_factor_count?: NewFilingFact | null;
  points?: { label: string; revenue?: number | null; profit_after_tax?: number | null; source_page?: number | null }[];
  max_value?: number;
};

export type NewFilingSummary = {
  slug: string;
  url_path: string;
  headline: string;
  dek: string;
  company_name: string;
  filing_date: string;
  document_type: string;
  document_url: string;
  quality_state: 'pass' | 'review' | string;
  verified_fact_count: number;
  generated_at: string;
};

export type NewFilingArticle = NewFilingSummary & {
  detail_url: string;
  pdf_pages: number;
  pdf_text_chars: number;
  quality: {
    state: 'pass' | 'review' | 'fail' | string;
    publishable: boolean;
    verified_fact_count: number;
    warnings?: unknown[];
    failures?: unknown[];
  };
  citation_validation: {
    checked_count: number;
    repaired_count: number;
    redacted_count: number;
    redaction_rate: number | null;
    repair_rate: number | null;
  };
  article: {
    summary: NewFilingFact[];
    blocks?: NewFilingArticleBlock[];
    sections: NewFilingSection[];
    citations: NewFilingFact[];
  };
  facts: Record<string, unknown>;
};

type IndexDoc = {
  generated_at: string;
  count: number;
  items: NewFilingSummary[];
};

const readJsonIf = <T>(rel: string): T | null => {
  const path = join(ROOT, rel);
  if (!existsSync(path)) return null;
  return JSON.parse(readFileSync(path, 'utf-8')) as T;
};

export const listNewFilings = (): NewFilingSummary[] =>
  readJsonIf<IndexDoc>('new_filings/index.json')?.items ?? [];

export const getNewFiling = (slug: string): NewFilingArticle | null =>
  readJsonIf<NewFilingArticle>(`new_filings/${slug}/article.json`);

export const listNewFilingPaths = (): { slug: string }[] =>
  listNewFilings().map((item) => ({ slug: item.slug }));

export const newFilingsUpdatedAt = (): string | null =>
  readJsonIf<IndexDoc>('new_filings/index.json')?.generated_at ?? null;
