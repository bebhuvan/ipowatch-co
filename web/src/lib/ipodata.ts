// V3 data-access layer. Reads the public dataset at build time behind a
// single configurable root (IPO_DATA_DIR), so swapping to an R2-synced/
// artifact-synced path later is a one-line change.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type {
  Issue, IssueSummary, Company, Prospectus, Trajectory, QualityState,
} from './ipotypes';

// Resolve the data root once. Prefer IPO_DATA_DIR; fall back to the in-repo
// self-contained data/ipo_watch_v3 tree.
const FALLBACK = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../../data/ipo_watch_v3',
);
export const ROOT = process.env.IPO_DATA_DIR?.trim() || FALLBACK;

const readJSON = <T>(rel: string): T =>
  JSON.parse(fs.readFileSync(path.join(ROOT, rel), 'utf-8')) as T;

const readJSONIf = <T>(rel: string): T | null => {
  const p = path.join(ROOT, rel);
  return fs.existsSync(p) ? (JSON.parse(fs.readFileSync(p, 'utf-8')) as T) : null;
};

/** Index/facet documents wrap their rows in `items`. */
interface IndexDoc<T> { items: T[]; count?: number }

// ── Quality filter ──────────────────────────────────────────────────────
const RENDERABLE = new Set<QualityState>(['clean', 'review']);
export const isRenderable = (s?: QualityState | null): boolean =>
  s != null && RENDERABLE.has(s);
/** Drop anything not clean/review before rendering. */
export const renderable = (items: IssueSummary[]): IssueSummary[] =>
  items.filter((i) => isRenderable(i.data_quality_state));

// ── Issues ──────────────────────────────────────────────────────────────
export const allIssues = (): IssueSummary[] =>
  readJSON<IndexDoc<IssueSummary>>('issues/index.json').items;

export const issue = (slug: string): Issue =>
  readJSON<Issue>(`issues/by-slug/${slug}.json`);

export const issueIf = (slug: string): Issue | null =>
  readJSONIf<Issue>(`issues/by-slug/${slug}.json`);

// By-status index files are written lowercase by the exporter
// (site_v3/export.py: `status.lower()`), so normalise the key here.
export const byStatus = (s: string): IssueSummary[] =>
  readJSONIf<IndexDoc<IssueSummary>>(`issues/by-status/${s.toLowerCase()}.json`)?.items ?? [];

export const byKind = (k: string): IssueSummary[] =>
  readJSONIf<IndexDoc<IssueSummary>>(`issues/by-kind/${k}.json`)?.items ?? [];

export const byYear = (y: string | number): IssueSummary[] =>
  readJSONIf<IndexDoc<IssueSummary>>(`issues/by-year/${y}.json`)?.items ?? [];

export const availableYears = (): number[] => {
  const dir = path.join(ROOT, 'issues/by-year');
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir)
    .filter((f) => /^\d{4}\.json$/.test(f))
    .map((f) => Number(f.slice(0, 4)))
    .sort((a, b) => b - a);
};

// ── Prospectus (optional — the differentiator) ────────────────────────────
export const prospectus = (slug: string): Prospectus | null =>
  readJSONIf<Prospectus>(`issues/${slug}/prospectus_facts.json`);

export const hasProspectus = (slug: string): boolean =>
  fs.existsSync(path.join(ROOT, 'issues', slug, 'prospectus_facts.json'));

// ── Companies ─────────────────────────────────────────────────────────────
export const allCompanies = (): Array<Pick<Company, 'slug' | 'company_name' | 'issue_count' | 'url_path'>> =>
  readJSON<IndexDoc<Company>>('companies/index.json').items;

export const company = (slug: string): Company | null =>
  readJSONIf<Company>(`companies/by-slug/${slug}.json`);

// ── Trajectories (optional — live subscription chart) ──────────────────────
export const trajectory = (slug: string): Trajectory | null =>
  readJSONIf<Trajectory>(`trajectories/${slug}.json`);

// ── Manifest (pin dataset_version in the build) ────────────────────────────
export const manifest = () => readJSON<Record<string, unknown>>('manifest.json');

export * from './formatv2';
