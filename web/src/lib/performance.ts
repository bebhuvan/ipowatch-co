import { allIssues, issue as readIssue } from './ipodata';
import { publicIssueSlug } from './issues';
import type { Issue, IssueSummary } from './ipotypes';

export const PAGE_SIZE = 50;

export type PerfRow = {
  slug: string;
  url_path: string;
  company_name: string;
  symbol: string | null;
  issue_type: string | null;
  exchange_platform: string | null;
  close_date?: string | null;
  open_date?: string | null;
  listing_date: string | null;
  issue_price: number | null;
  listing_open: number | null;
  listing_close: number | null;
  current_price: number | null;
  listing_open_return: number | null;
  listing_close_return: number | null;
  current_return: number | null;
  listing_price_source: string;
  current_price_source: string;
  benchmarks: Record<string, unknown>;
};

const rupees = (paise?: number | null): number | null => paise == null ? null : paise / 100;
const pct = (bps?: number | null): number | null => bps == null ? null : bps / 100;

const percentChange = (from: number | null | undefined, to: number | null | undefined): number | null => {
  if (from == null || to == null || !Number.isFinite(from) || !Number.isFinite(to) || from === 0) return null;
  return ((to - from) / from) * 100;
};

const sourceFor = (issue: Issue, field: string): string => {
  const source = issue.field_provenance?.[field]?.source;
  return source && ['kite', 'yahoo'].includes(source) ? source : 'source';
};

const exchangePlatform = (summary: IssueSummary, issue: Issue): string | null => {
  const names = new Set<string>();
  for (const source of issue.sources ?? []) {
    const s = String(source.source ?? '').toUpperCase();
    if (s === 'NSE' || s === 'BSE') names.add(s);
  }
  const exchange = names.size ? Array.from(names).sort().join('/') : null;
  return [exchange, summary.board_type === 'SME Board' ? 'SME' : null].filter(Boolean).join(' ') || null;
};

const rowKey = (row: PerfRow): string => [
  row.company_name.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim(),
  row.listing_date ?? row.close_date ?? '',
  row.issue_price ?? '',
].join('|');

const rowScore = (row: PerfRow): number =>
  (row.current_price != null ? 4 : 0)
  + (row.listing_close != null ? 2 : 0)
  + (row.listing_open != null ? 1 : 0)
  + (row.current_price_source !== 'source' ? 1 : 0);

let rowsCache: PerfRow[] | null = null;

export const performanceRows = (): PerfRow[] => {
  if (rowsCache) return rowsCache;
  const unique = new Map<string, PerfRow>();
  for (const summary of allIssues()) {
    if (summary.issue_type !== 'IPO') continue;
    if (summary.issue_price_paise == null) continue;
    const detail = readIssue(summary.slug);
    const perf = detail.listing_performance ?? {};
    const pricing = detail.pricing ?? {};
    const issuePrice = rupees(pricing.issue_price_paise);
    const listingOpen = rupees(perf.listing_open_price_paise);
    const listingClose = rupees(perf.listing_close_price_paise);
    const currentPrice = rupees(perf.current_price_paise);
    if (issuePrice == null || (listingOpen == null && listingClose == null && currentPrice == null)) continue;
    const row: PerfRow = {
      slug: summary.slug,
      url_path: `/ipos/${publicIssueSlug(summary.slug)}/`,
      company_name: summary.company_name,
      symbol: summary.symbol,
      issue_type: summary.issue_type,
      exchange_platform: exchangePlatform(summary, detail),
      listing_date: summary.listing_date,
      open_date: summary.open_date,
      close_date: summary.close_date,
      issue_price: issuePrice,
      listing_open: listingOpen,
      listing_close: listingClose,
      current_price: currentPrice,
      listing_open_return: percentChange(issuePrice, listingOpen),
      listing_close_return: pct(perf.listing_gain_bps) ?? percentChange(issuePrice, listingClose),
      current_return: pct(perf.current_gain_bps) ?? percentChange(issuePrice, currentPrice),
      listing_price_source: sourceFor(detail, 'listing_performance.listing_close_price_paise'),
      current_price_source: sourceFor(detail, 'listing_performance.current_price_paise'),
      benchmarks: {},
    };
    const key = rowKey(row);
    const prev = unique.get(key);
    if (!prev || rowScore(row) > rowScore(prev)) unique.set(key, row);
  }
  rowsCache = Array.from(unique.values())
    .sort((a, b) => (b.listing_date ?? b.close_date ?? '').localeCompare(a.listing_date ?? a.close_date ?? ''));
  return rowsCache;
};

const median = (values: number[]): number | null => {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!sorted.length) return null;
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
};

const ratio = (num: number, den: number): number | null => den ? (num / den) * 100 : null;

const compact = (row: PerfRow) => ({
  slug: row.slug,
  url_path: row.url_path,
  company_name: row.company_name,
  listing_date: row.listing_date,
  current_return: row.current_return,
  listing_close_return: row.listing_close_return,
});

const topRows = (rows: PerfRow[], key: keyof PerfRow, reverse: boolean, limit: number) => rows
  .filter((row) => typeof row[key] === 'number')
  .sort((a, b) => reverse ? Number(b[key]) - Number(a[key]) : Number(a[key]) - Number(b[key]))
  .slice(0, limit)
  .map(compact);

const returnBuckets = (rows: PerfRow[], key: keyof PerfRow) => {
  const buckets = [
    { label: '< -50%', min: -Infinity, max: -50 },
    { label: '-50% to 0%', min: -50, max: 0 },
    { label: '0% to 50%', min: 0, max: 50 },
    { label: '50% to 100%', min: 50, max: 100 },
    { label: '100%+', min: 100, max: Infinity },
  ];
  return buckets.map((bucket) => ({
    label: bucket.label,
    count: rows.filter((row) => {
      const value = row[key];
      return typeof value === 'number' && value >= bucket.min && value < bucket.max;
    }).length,
  }));
};

export const performanceSummary = () => {
  const rows = performanceRows();
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const byYear = new Map<string, PerfRow[]>();
  for (const row of rows) {
    const year = (row.listing_date ?? row.close_date ?? 'undated').slice(0, 4);
    byYear.set(year, [...(byYear.get(year) ?? []), row]);
  }
  const currentRows = rows.filter((row) => row.current_return != null);
  const dayRows = rows.filter((row) => row.listing_close_return != null);
  return {
    schema_version: '3.0.0',
    source: 'data/ipo_watch_v3',
    total_rows: rows.length,
    page_size: PAGE_SIZE,
    page_count: pageCount,
    with_kite_listing: rows.filter((row) => row.listing_price_source === 'kite').length,
    with_kite_current: rows.filter((row) => row.current_price_source === 'kite').length,
    with_yahoo_listing: rows.filter((row) => row.listing_price_source === 'yahoo').length,
    with_yahoo_current: rows.filter((row) => row.current_price_source === 'yahoo').length,
    with_day_one: dayRows.length,
    with_current: currentRows.length,
    years: Array.from(byYear.entries()).sort((a, b) => b[0].localeCompare(a[0])).map(([year, bucket]) => {
      const dayReturns = bucket.map((r) => r.listing_close_return).filter((v): v is number => v != null);
      const currentReturns = bucket.map((r) => r.current_return).filter((v): v is number => v != null);
      return {
        year,
        count: bucket.length,
        with_current: currentReturns.length,
        median_day_one: median(dayReturns),
        median_current: median(currentReturns),
        positive_day_one_pct: ratio(dayReturns.filter((v) => v > 0).length, dayReturns.length),
      };
    }),
    source_mix: rows.reduce<Record<string, number>>((acc, row) => {
      const key = row.current_price_source ?? 'source';
      acc[key] = (acc[key] ?? 0) + 1;
      return acc;
    }, {}),
    benchmarks: [],
    best_since_issue: topRows(currentRows, 'current_return', true, 8),
    worst_since_issue: topRows(currentRows, 'current_return', false, 8),
    best_day_one: topRows(dayRows, 'listing_close_return', true, 8),
    worst_day_one: topRows(dayRows, 'listing_close_return', false, 8),
    return_buckets: returnBuckets(currentRows, 'current_return'),
  };
};

export const performanceIndex = () => ({ schema_version: '3.0.0', source: 'data/ipo_watch_v3', rows: performanceRows() });

export const performancePage = (page: number) => {
  const rows = performanceRows();
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const start = (page - 1) * PAGE_SIZE;
  return {
    schema_version: '3.0.0',
    source: 'data/ipo_watch_v3',
    page,
    page_size: PAGE_SIZE,
    page_count: pageCount,
    total_rows: rows.length,
    rows: rows.slice(start, start + PAGE_SIZE),
  };
};
