// V3-backed data access helpers. The UI still consumes the older display
// shape, so this file adapts canonical machine units from data/ipo_watch_v3 into
// rupees, percent values, and compact page models.

import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import {
  ROOT as v2Root,
  allIssues as allV2Issues,
  issueIf as v2IssueIf,
  byStatus as v2ByStatus,
  prospectus as v2Prospectus,
  trajectory as v2Trajectory,
  manifest as v2Manifest,
} from './ipodata';
import type {
  Issue as V2Issue,
  IssueSummary as V2IssueSummary,
  TrajectoryObservation as V2TrajectoryObservation,
  Company as V2Company,
} from './ipotypes';

const legacyDataRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../../../data/site');

export type PriceBand = { min: number | null; max: number | null; text: string | null };

export type IssueSummary = {
  id: string;
  slug: string;
  url_path: string;
  title: string;
  company_name: string;
  status: 'current' | 'upcoming' | 'past' | 'document' | 'unknown';
  issue_type: string | null;
  exchange_platform: string | null;
  open_date: string | null;
  close_date: string | null;
  listing_date: string | null;
  price_band: PriceBand;
  listing_day_gain: number | null;
  quality_state: 'clean' | 'review' | 'blocked';
};

export type TrajectoryObservation = {
  observed_at: string;
  source: 'bse' | 'nse' | string;
  source_updated_at?: string | null;
  categories: Record<string, { times: number | null; shares_offered: number | null; shares_bid: number | null }>;
  total: { times: number | null; shares_offered: number | null; shares_bid: number | null };
};

export type DemandCurve = {
  observed_at: string;
  source: string | null;
  scope: string;
  source_updated_at: string | null;
  points: { price: number; cumulative_quantity: number }[];
};

export type SubscriptionCategory = {
  category: string;
  shares_offered: number | null;
  shares_bid: number | null;
  times: number | null;
  applications?: number | null;
};

export type SubscriptionBook = {
  categories: SubscriptionCategory[];
  total_times: number | null;
};

export type IssueSource = {
  source: string;
  endpoint: string;
  record_id: string;
  source_record_id: string;
  observed_at: string;
};

export type IssueDetail = {
  id: string;
  slug: string;
  url_path: string;
  title: string;
  company: { id: string; name: string; slug: string; url_path: string; symbol: string | null };
  classification: {
    status: IssueSummary['status'];
    issue_type: string | null;
    security_type: string | null;
    exchange_platform: string | null;
  };
  timeline: { open_date: string | null; close_date: string | null; listing_date: string | null };
  pricing: { price_band: PriceBand; issue_price: number | null; face_value: number | null; lot_size_shares?: number | null };
  parties: {
    lead_managers: string[];
    registrar: string | null;
    sponsor_bank: string | null;
  };
  exchange_issue_info: { title: string; value: string; href?: string | null }[];
  issue_size: { text: string | null; shares_offered: number | null; amount: number | null };
  subscription: {
    shares_bid: number | null;
    times: number | null;
    trajectory: TrajectoryObservation[];
    demand_curves: DemandCurve[];
    consolidated: SubscriptionBook | null;
    by_exchange: Record<string, SubscriptionBook>;
  };
  listing_performance: {
    listing_day_open: number | null;
    listing_day_close: number | null;
    listing_day_gain: number | null;
    current_price: number | null;
    gain_loss: number | null;
    stock_url: string | null;
  };
  exchange_details: Record<string, any>;
  documents: { type: string; url: string; date?: string; source_date?: string }[];
  prospectus_facts: Record<string, any> | null;
  data_quality: { state: string; error_count: number; warning_count: number };
  sources: IssueSource[];
  redactions?: { field: string; reason: string; count?: number }[];
};

export type CompanyIndexEntry = {
  id: string;
  name: string;
  slug: string;
  url_path: string;
  issue_count: number;
  latest_issue_date: string | null;
};

export type SiteManifest = Record<string, any> & {
  generated_at?: string;
  counts: Record<string, any>;
};

const paiseToRupees = (value: number | null | undefined): number | null =>
  value == null ? null : value / 100;

const bpsToPct = (value: number | null | undefined): number | null =>
  value == null ? null : value / 100;

const timesToNumber = (value: string | null | undefined): number | null => {
  if (value == null) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const quality = (value: string | null | undefined): IssueSummary['quality_state'] =>
  value === 'quarantine' ? 'blocked' : value === 'review' ? 'review' : 'clean';

const legacyStatus = (value: string | null | undefined): IssueSummary['status'] => {
  if (value === 'Open') return 'current';
  if (value === 'Upcoming' || value === 'Filed') return 'upcoming';
  if (value === 'Listed' || value === 'Closed' || value === 'Withdrawn') return 'past';
  return 'unknown';
};

const exchangePlatform = (row: Pick<V2IssueSummary, 'board_type'> & { sources?: Array<Record<string, unknown>> }): string | null => {
  const names = new Set<string>();
  for (const source of row.sources ?? []) {
    const s = String(source.source ?? '').toUpperCase();
    if (s === 'NSE' || s === 'BSE') names.add(s);
  }
  const exchange = names.size ? Array.from(names).sort().join('/') : null;
  if (!exchange && !row.board_type) return null;
  return [exchange, row.board_type === 'SME Board' ? 'SME' : null].filter(Boolean).join(' ');
};

const priceBand = (lo?: number | null, hi?: number | null): PriceBand => {
  const min = paiseToRupees(lo);
  const max = paiseToRupees(hi);
  const text = min == null && max == null ? null : min === max ? String(max ?? min) : `${min ?? '—'}-${max ?? '—'}`;
  return { min, max, text };
};

export const normalizeIssueType = (t: string | null | undefined): string => {
  const s = (t ?? '').toLowerCase().replace(/[\s_-]+/g, '');
  if (!s || s === 'ipo' || s === 'initialpublicoffer' || s === 'initialpublicoffering') return 'IPO';
  if (s === 'fpo' || s === 'followonpublicoffer') return 'FPO';
  if (s === 'buyback' || s === 'buy' || s === 'tender') return s === 'tender' ? 'Tender' : 'Buyback';
  if (s === 'ofs' || s === 'offerforsale') return 'OFS';
  if (s === 'rights' || s === 'right' || s === 'rightsissue') return 'Rights';
  if (s === 'invit' || s === 'invits') return 'InvIT';
  if (s === 'reit' || s === 'reits') return 'REIT';
  if (s === 'callmoney' || s === 'cmn') return 'Call Money';
  if (s === 'ipp') return 'IPP';
  if (s === 'qip') return 'QIP';
  if (s === 'ncd' || s === 'dpi' || s === 'debt') return 'NCD';
  if (s === 'others' || s === 'other') return 'Others';
  return 'Others';
};

export const isEquityIPOType = (issueType: string | null | undefined): boolean => {
  const normalized = normalizeIssueType(issueType);
  return normalized === 'IPO' || normalized === 'FPO';
};

export type IssueSectionKey = 'ipos' | 'buybacks' | 'ofs' | 'rights' | 'debt' | 'reits' | 'invits' | 'call-money' | 'public-issues';

export const issueSectionForType = (issueType: string | null | undefined): IssueSectionKey => {
  const normalized = normalizeIssueType(issueType);
  if (normalized === 'IPO' || normalized === 'FPO') return 'ipos';
  if (normalized === 'Buyback' || normalized === 'Tender') return 'buybacks';
  if (normalized === 'OFS') return 'ofs';
  if (normalized === 'Rights') return 'rights';
  if (normalized === 'NCD') return 'debt';
  if (normalized === 'REIT') return 'reits';
  if (normalized === 'InvIT') return 'invits';
  if (normalized === 'Call Money') return 'call-money';
  return 'public-issues';
};

export const issueSectionLabel = (section: IssueSectionKey): string => ({
  ipos: 'IPOs',
  buybacks: 'Buybacks',
  ofs: 'Offers for sale',
  rights: 'Rights issues',
  debt: 'Debt issues',
  reits: 'REITs',
  invits: 'InvITs',
  'call-money': 'Call money',
  'public-issues': 'Public issues',
}[section]);

export const nonEquityIssueSections: IssueSectionKey[] = ['buybacks', 'ofs', 'rights', 'debt', 'reits', 'invits', 'call-money', 'public-issues'];

const HASH_SUFFIX_RE = /-[0-9a-f]{6}$/;

const cleanSlugStem = (slug: string | null | undefined): string => {
  const stem = String(slug ?? '').replace(HASH_SUFFIX_RE, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return stem || 'issue';
};

const issueSortDate = (row: Pick<V2IssueSummary, 'listing_date' | 'close_date' | 'open_date'>): string | null =>
  row.listing_date ?? row.close_date ?? row.open_date ?? null;

let publicIssueSlugCache: Map<string, string> | null = null;

const publicIssueSlugMap = (): Map<string, string> => {
  if (publicIssueSlugCache) return publicIssueSlugCache;
  const rows = allV2Issues().filter((row) => quality(row.data_quality_state) !== 'blocked');
  const groups = new Map<string, V2IssueSummary[]>();
  for (const row of rows) {
    const section = issueSectionForType(row.issue_type);
    const base = row.public_slug || cleanSlugStem(row.slug);
    const key = `${section}:${base}`;
    groups.set(key, [...(groups.get(key) ?? []), row]);
  }

  const usedBySection = new Map<IssueSectionKey, Set<string>>();
  const used = (section: IssueSectionKey) => {
    const found = usedBySection.get(section);
    if (found) return found;
    const created = new Set<string>();
    usedBySection.set(section, created);
    return created;
  };

  const out = new Map<string, string>();
  for (const [key, group] of Array.from(groups.entries()).sort(([a], [b]) => a.localeCompare(b))) {
    const [sectionRaw, base] = key.split(':', 2);
    const section = sectionRaw as IssueSectionKey;
    const taken = used(section);
    const sorted = group.slice().sort((a, b) =>
      (issueSortDate(a) ?? '').localeCompare(issueSortDate(b) ?? '') || a.slug.localeCompare(b.slug)
    );
    for (const row of sorted) {
      const date = issueSortDate(row);
      const year = date?.slice(0, 4);
      const shortId = row.slug.match(/[0-9a-f]{6}$/)?.[0];
      const candidates = group.length === 1
        ? [base]
        : [
            year ? `${base}-${year}` : null,
            date ? `${base}-${date}` : null,
            shortId ? `${base}-${shortId}` : null,
            row.slug,
          ];
      const chosen = candidates.find((candidate): candidate is string => Boolean(candidate && !taken.has(candidate))) ?? row.slug;
      taken.add(chosen);
      out.set(row.slug, chosen);
    }
  }
  publicIssueSlugCache = out;
  return out;
};

export const publicIssueSlug = (slug: string): string => publicIssueSlugMap().get(slug) ?? cleanSlugStem(slug);
const issuePath = (slug: string, issueType?: string | null): string => `/${issueSectionForType(issueType)}/${publicIssueSlug(slug)}/`;

let publicCompanySlugCache: Map<string, string> | null = null;

const publicCompanySlugMap = (): Map<string, string> => {
  if (publicCompanySlugCache) return publicCompanySlugCache;
  const dir = join(v2Root, 'companies', 'by-slug');
  const companies = existsSync(dir)
    ? readdirSync(dir).filter((f) => f.endsWith('.json')).map((file) => readJsonIf<V2Company>(join(dir, file))).filter((company): company is V2Company => Boolean(company))
    : [];
  const groups = new Map<string, V2Company[]>();
  for (const company of companies) {
    const base = company.public_slug || cleanSlugStem(company.slug);
    groups.set(base, [...(groups.get(base) ?? []), company]);
  }
  const taken = new Set<string>();
  const out = new Map<string, string>();
  for (const [base, group] of Array.from(groups.entries()).sort(([a], [b]) => a.localeCompare(b))) {
    const sorted = group.slice().sort((a, b) => a.slug.localeCompare(b.slug));
    for (const company of sorted) {
      const shortId = company.slug.match(/[0-9a-f]{6}$/)?.[0];
      const candidates = group.length === 1 ? [base] : [base, shortId ? `${base}-${shortId}` : null, company.slug];
      const chosen = candidates.find((candidate): candidate is string => Boolean(candidate && !taken.has(candidate))) ?? company.slug;
      taken.add(chosen);
      out.set(company.slug, chosen);
    }
  }
  publicCompanySlugCache = out;
  return out;
};

const publicCompanySlug = (slug: string): string => publicCompanySlugMap().get(slug) ?? cleanSlugStem(slug);
const companyPath = (slug: string): string => `/companies/${publicCompanySlug(slug)}/`;

const summaryFromV2 = (row: V2IssueSummary): IssueSummary => ({
  id: row.slug,
  slug: row.slug,
  url_path: issuePath(row.slug, row.issue_type),
  title: row.company_name,
  company_name: row.company_name,
  status: legacyStatus(row.status),
  issue_type: row.issue_type,
  exchange_platform: exchangePlatform({ ...row }),
  open_date: row.open_date,
  close_date: row.close_date,
  listing_date: row.listing_date,
  price_band: priceBand(row.price_band_lower_paise, row.price_band_upper_paise),
  listing_day_gain: bpsToPct(row.listing_gain_bps),
  quality_state: quality(row.data_quality_state),
});

const sortDateDesc = (a: IssueSummary, b: IssueSummary) =>
  (b.listing_date ?? b.close_date ?? b.open_date ?? '').localeCompare(a.listing_date ?? a.close_date ?? a.open_date ?? '');

const sortDateAsc = (a: IssueSummary, b: IssueSummary) =>
  (a.open_date ?? a.close_date ?? a.listing_date ?? '9999').localeCompare(b.open_date ?? b.close_date ?? b.listing_date ?? '9999');

export const getCurrent = (): IssueSummary[] =>
  v2ByStatus('Open').map(summaryFromV2).filter((i) => i.quality_state !== 'blocked' && isEquityIPOType(i.issue_type)).sort(sortDateAsc);

export const getUpcoming = (): IssueSummary[] =>
  [...v2ByStatus('Upcoming'), ...v2ByStatus('Filed')]
    .map(summaryFromV2)
    .filter((i) => i.quality_state !== 'blocked' && isEquityIPOType(i.issue_type))
    .sort(sortDateAsc);

const getAllRecentHistorical = (): IssueSummary[] => {
  const rows = allV2Issues()
    .filter((i) => i.status === 'Listed' || i.status === 'Closed' || i.status === 'Withdrawn')
    .map(summaryFromV2)
    .filter((i) => i.quality_state !== 'blocked')
    .sort(sortDateDesc);
  return rows;
};

export const getRecentHistorical = (count: number): IssueSummary[] => {
  const rows = getAllRecentHistorical().filter((i) => isEquityIPOType(i.issue_type));
  return count > 0 ? rows.slice(0, count) : rows;
};

export const getManifest = (): SiteManifest => {
  const manifest = v2Manifest() as Record<string, any>;
  return {
    ...manifest,
    counts: manifest.counts ?? {
      issues: manifest.issues_published ?? manifest.issues_total ?? 0,
      companies: manifest.companies_total ?? 0,
      trajectories: manifest.trajectories_total ?? 0,
    },
  };
};

let companyEntriesCache: CompanyIndexEntry[] | null = null;
let issueCompanyCache: Map<string, CompanyIndexEntry> | null = null;

const readJsonIf = <T>(path: string): T | null => {
  if (!existsSync(path)) return null;
  return JSON.parse(readFileSync(path, 'utf-8')) as T;
};

const companyEntries = (): CompanyIndexEntry[] => {
  if (companyEntriesCache) return companyEntriesCache;
  const dir = join(v2Root, 'companies', 'by-slug');
  const entries: CompanyIndexEntry[] = [];
  const issueMap = new Map<string, CompanyIndexEntry>();
  if (existsSync(dir)) {
    for (const file of readdirSync(dir).filter((f) => f.endsWith('.json'))) {
      const company = readJsonIf<V2Company>(join(dir, file));
      if (!company) continue;
      const latest = (company.issues ?? [])
        .map((issue) => issue.listing_date ?? issue.close_date ?? issue.open_date ?? null)
        .filter((d): d is string => Boolean(d))
        .sort()
        .at(-1) ?? null;
      const entry: CompanyIndexEntry = {
        id: company.slug,
        name: company.company_name,
        slug: publicCompanySlug(company.slug),
        url_path: companyPath(company.slug),
        issue_count: company.issue_count,
        latest_issue_date: latest,
      };
      entries.push(entry);
      for (const issue of company.issues ?? []) issueMap.set(issue.slug, entry);
    }
  }
  companyEntriesCache = entries.sort((a, b) => b.issue_count - a.issue_count || a.name.localeCompare(b.name));
  issueCompanyCache = issueMap;
  return companyEntriesCache;
};

const companyForIssue = (issue: V2Issue): CompanyIndexEntry => {
  if (!issueCompanyCache) companyEntries();
  const found = issueCompanyCache?.get(issue.slug);
  if (found) return found;
  return {
    id: issue.identity.slug,
    name: issue.identity.company_name,
    slug: publicCompanySlug(issue.identity.slug),
    url_path: companyPath(issue.identity.slug),
    issue_count: 1,
    latest_issue_date: issue.timeline?.listing_date ?? issue.timeline?.close_date ?? issue.timeline?.open_date ?? null,
  };
};

const sourceRows = (issue: V2Issue): IssueSource[] =>
  (issue.sources ?? []).map((source, idx) => ({
    source: String(source.source ?? ''),
    endpoint: String(source.endpoint ?? ''),
    record_id: `${issue.slug}:${idx}`,
    source_record_id: String(source.record_id ?? source.url ?? ''),
    observed_at: String(source.snapshot_at ?? ''),
  }));

const documentRows = (issue: V2Issue): IssueDetail['documents'] => {
  const docs = issue.documents ?? {};
  const labels: Record<string, string> = {
    drhp_url: 'DRHP',
    rhp_url: 'RHP',
    prospectus_url: 'Prospectus',
    basis_allotment_url: 'Basis of allotment',
  };
  return Object.entries(docs)
    .filter((entry): entry is [string, string] => typeof entry[1] === 'string' && entry[1].length > 0)
    .map(([key, url]) => ({ type: labels[key] ?? key, url }));
};

const hrefFromText = (value: unknown): string | null => {
  if (typeof value !== 'string') return null;
  return value.startsWith('http://') || value.startsWith('https://') ? value : null;
};

const trajectoryRows = (slug: string): TrajectoryObservation[] =>
  (v2Trajectory(slug)?.observations ?? []).map((row: V2TrajectoryObservation) => ({
    observed_at: row.observed_at,
    source: row.source ?? '',
    source_updated_at: row.source_updated_at,
    total: row.total,
    categories: row.categories,
  }));

const demandCurveRows = (slug: string): DemandCurve[] =>
  (v2Trajectory(slug)?.demand_curves ?? []).map((curve: any) => ({
    observed_at: String(curve.observed_at ?? ''),
    source: curve.source ?? null,
    scope: String(curve.scope ?? ''),
    source_updated_at: curve.source_updated_at ?? null,
    points: ((curve.points ?? []) as any[])
      .map((point) => ({
        price: paiseToRupees(point.price_paise) ?? 0,
        cumulative_quantity: Number(point.cumulative_quantity ?? 0),
      }))
      .filter((point) => point.price > 0 && point.cumulative_quantity > 0),
  })).filter((curve) => curve.points.length > 0);

const subscriptionBook = (book: any): SubscriptionBook | null => {
  if (!book || !Array.isArray(book.categories)) return null;
  const categories = book.categories
    .map((row: any) => ({
      category: String(row.category ?? ''),
      shares_offered: typeof row.shares_offered === 'number' ? row.shares_offered : null,
      shares_bid: typeof row.shares_bid === 'number' ? row.shares_bid : null,
      times: timesToNumber(row.times_x),
      applications: typeof row.applications === 'number' ? row.applications : null,
    }))
    .filter((row: SubscriptionCategory) => row.category);
  if (!categories.length) return null;
  return {
    categories,
    total_times: timesToNumber(book.total_times_x),
  };
};

const exchangeSubscriptionBooks = (subscription: any): Record<string, SubscriptionBook> => {
  const out: Record<string, SubscriptionBook> = {};
  const byExchange = subscription?.by_exchange ?? {};
  for (const [exchange, book] of Object.entries(byExchange)) {
    const parsed = subscriptionBook(book);
    if (parsed) out[exchange] = parsed;
  }
  return out;
};

const stringList = (value: unknown): string[] => {
  if (Array.isArray(value)) return value.map((v) => String(v ?? '').trim()).filter(Boolean);
  const text = String(value ?? '').trim();
  return text ? [text] : [];
};

const canonicalIssueInfoRows = (issue: V2Issue): IssueDetail['exchange_issue_info'] => {
  const pricing = issue.pricing ?? {};
  const timeline = issue.timeline ?? {};
  const parties = issue.parties ?? {};
  const documents = issue.documents ?? {};
  const rows: IssueDetail['exchange_issue_info'] = [];
  const push = (title: string, value: unknown) => {
    if (value == null || value === '') return;
    const text = String(value);
    rows.push({ title, value: text, href: hrefFromText(text) });
  };
  push('Symbol', issue.identity?.symbol);
  if (timeline.open_date || timeline.close_date) {
    push('Issue period', [timeline.open_date, timeline.close_date].filter(Boolean).join(' to '));
  }
  if (pricing.issue_size_shares) push('Issue size', `${Number(pricing.issue_size_shares).toLocaleString('en-IN')} shares`);
  if (pricing.price_band_lower_paise || pricing.price_band_upper_paise) {
    const lo = paiseToRupees(pricing.price_band_lower_paise);
    const hi = paiseToRupees(pricing.price_band_upper_paise);
    push('Price range', lo === hi ? `Rs.${hi}` : `Rs.${lo ?? '-'} to Rs.${hi ?? '-'}`);
  }
  push('Lot size', pricing.market_lot ?? pricing.lot_size_shares);
  if (pricing.face_value_paise) push('Face value', `Rs.${paiseToRupees(pricing.face_value_paise)}`);
  if (pricing.tick_size_paise) push('Tick size', `Rs.${paiseToRupees(pricing.tick_size_paise)}`);
  push('Book running lead managers', stringList(parties.lead_managers).join(', '));
  push('Sponsor bank', parties.sponsor_bank);
  push('Name of the Registrar', parties.registrar);
  push('DRHP', documents.drhp_url);
  push('Red Herring Prospectus', documents.rhp_url);
  push('Prospectus', documents.prospectus_url);
  return rows;
};

const stockUrl = (symbol: string | null | undefined): string | null =>
  symbol ? `https://finance.yahoo.com/quote/${encodeURIComponent(symbol)}.NS` : null;

const issueSizeText = (amount: number | null): string | null => {
  if (amount == null) return null;
  if (Math.abs(amount) >= 1e7) return `₹${(amount / 1e7).toFixed(2)} Cr`;
  if (Math.abs(amount) >= 1e5) return `₹${(amount / 1e5).toFixed(2)} L`;
  return `₹${amount.toLocaleString('en-IN')}`;
};

const detailFromV2 = (issue: V2Issue): IssueDetail => {
  const company = companyForIssue(issue);
  const pricing = issue.pricing ?? {};
  const perf = issue.listing_performance ?? {};
  const amount = paiseToRupees(pricing.issue_size_paise);
  return {
    id: issue.slug,
    slug: issue.slug,
    url_path: issuePath(issue.slug, issue.identity.issue_type),
    title: issue.identity.company_name,
    company: {
      id: company.id,
      name: company.name,
      slug: company.slug,
      url_path: company.url_path,
      symbol: issue.identity.symbol ?? null,
    },
    classification: {
      status: legacyStatus(issue.identity.status),
      issue_type: issue.identity.issue_type ?? null,
      security_type: issue.identity.issue_type ?? null,
      exchange_platform: exchangePlatform({ board_type: issue.identity.board_type ?? null, sources: issue.sources }),
    },
    timeline: {
      open_date: issue.timeline?.open_date ?? null,
      close_date: issue.timeline?.close_date ?? null,
      listing_date: issue.timeline?.listing_date ?? null,
    },
    pricing: {
      price_band: priceBand(pricing.price_band_lower_paise, pricing.price_band_upper_paise),
      issue_price: paiseToRupees(pricing.issue_price_paise),
      face_value: paiseToRupees(pricing.face_value_paise),
      lot_size_shares: pricing.lot_size_shares ?? pricing.market_lot ?? null,
    },
    parties: {
      lead_managers: stringList(issue.parties?.lead_managers),
      registrar: issue.parties?.registrar ?? null,
      sponsor_bank: issue.parties?.sponsor_bank ?? null,
    },
    exchange_issue_info: canonicalIssueInfoRows(issue),
    issue_size: {
      text: issueSizeText(amount),
      shares_offered: pricing.issue_size_shares ?? null,
      amount,
    },
    subscription: {
      shares_bid: null,
      times: timesToNumber(issue.subscription?.overall_times_x),
      trajectory: trajectoryRows(issue.slug),
      demand_curves: demandCurveRows(issue.slug),
      consolidated: subscriptionBook(issue.subscription?.consolidated),
      by_exchange: exchangeSubscriptionBooks(issue.subscription),
    },
    listing_performance: {
      listing_day_open: paiseToRupees(perf.listing_open_price_paise),
      listing_day_close: paiseToRupees(perf.listing_close_price_paise),
      listing_day_gain: bpsToPct(perf.listing_gain_bps),
      current_price: paiseToRupees(perf.current_price_paise),
      gain_loss: bpsToPct(perf.current_gain_bps),
      stock_url: stockUrl(issue.identity.symbol),
    },
    exchange_details: issue.exchange_details ?? {},
    documents: documentRows(issue),
    prospectus_facts: v2Prospectus(issue.slug) as Record<string, any> | null,
    data_quality: {
      state: quality(issue.data_quality?.state),
      error_count: issue.data_quality?.errors?.length ?? 0,
      warning_count: issue.data_quality?.warnings?.length ?? 0,
    },
    sources: sourceRows(issue),
  };
};

export const getDetailBySlug = (slug: string): IssueDetail | null => {
  const issue = v2IssueIf(slug);
  return issue ? detailFromV2(issue) : null;
};

export const listFeaturedSlugs = (recentPastCount = 24): string[] => {
  const current = getCurrent().map((i) => i.slug);
  const upcoming = getUpcoming().map((i) => i.slug);
  const recent = getRecentHistorical(recentPastCount).map((i) => i.slug);
  return Array.from(new Set([...current, ...upcoming, ...recent]));
};

export const listAllBySlugFiles = (): string[] => {
  const dir = join(v2Root, 'issues', 'by-slug');
  return readdirSync(dir).filter((f: string) => f.endsWith('.json'));
};

const allIssueSummaries = (): IssueSummary[] =>
  allV2Issues()
    .map(summaryFromV2)
    .filter((i) => i.quality_state !== 'blocked');

export const getIssuesBySection = (section: IssueSectionKey): IssueSummary[] => {
  const rows = allIssueSummaries().filter((i) => issueSectionForType(i.issue_type) === section);
  return rows.sort((a, b) => {
    const statusRank = (status: IssueSummary['status']) =>
      status === 'current' ? 0 : status === 'upcoming' ? 1 : status === 'past' ? 2 : 3;
    const rank = statusRank(a.status) - statusRank(b.status);
    if (rank !== 0) return rank;
    if (a.status === 'current' || a.status === 'upcoming') return sortDateAsc(a, b);
    return sortDateDesc(a, b);
  });
};

export const listNonEquityIssueDetailPaths = (): { section: IssueSectionKey; slug: string; publicSlug: string }[] => {
  const out: { section: IssueSectionKey; slug: string; publicSlug: string }[] = [];
  for (const file of listAllBySlugFiles()) {
    const slug = file.replace(/\.json$/, '');
    const issue = v2IssueIf(slug);
    if (!issue || isEquityIPOType(issue.identity.issue_type)) continue;
    out.push({ section: issueSectionForType(issue.identity.issue_type), slug, publicSlug: publicIssueSlug(slug) });
  }
  return out;
};

export const listExtractedProspectusSlugs = (): string[] => {
  const dir = join(v2Root, 'issues');
  if (!existsSync(dir)) return [];
  return readdirSync(dir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .filter((slug) => {
      const doc = readJsonIf<Record<string, any>>(join(dir, slug, 'prospectus_facts.json'));
      return Boolean(doc?.deepseek?.used && doc?.quality?.state !== 'fail');
    });
};

export type YearStats = {
  year: number;
  total: number;
  mainboard: number;
  sme: number;
  with_gain: number;
  median_gain: number | null;
  positive_count: number;
  positive_pct: number | null;
  top_gainer: IssueSummary | null;
  worst: IssueSummary | null;
  by_exchange: { exchange: string; count: number }[];
};

const median = (xs: number[]): number | null => {
  if (xs.length === 0) return null;
  const sorted = xs.slice().sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
};

const allHistorical = (): IssueSummary[] => getRecentHistorical(0);

export const getYearStats = (year: number): YearStats => {
  const inYear = allHistorical().filter((i) => (i.listing_date ?? i.close_date ?? '').startsWith(String(year)));
  const mainboard = inYear.filter((i) => !(i.exchange_platform ?? '').includes('SME')).length;
  const sme = inYear.filter((i) => (i.exchange_platform ?? '').includes('SME')).length;
  const gains = inYear.map((i) => i.listing_day_gain).filter((g): g is number => g != null);
  const positiveCount = gains.filter((g) => g > 0).length;
  const sortedByGain = inYear
    .filter((i) => i.listing_day_gain != null)
    .sort((a, b) => (b.listing_day_gain ?? 0) - (a.listing_day_gain ?? 0));
  const byExchange = new Map<string, number>();
  for (const i of inYear) {
    const k = i.exchange_platform ?? 'unknown';
    byExchange.set(k, (byExchange.get(k) ?? 0) + 1);
  }
  return {
    year,
    total: inYear.length,
    mainboard,
    sme,
    with_gain: gains.length,
    median_gain: median(gains),
    positive_count: positiveCount,
    positive_pct: gains.length ? (positiveCount / gains.length) * 100 : null,
    top_gainer: sortedByGain[0] ?? null,
    worst: sortedByGain.length ? sortedByGain[sortedByGain.length - 1] : null,
    by_exchange: Array.from(byExchange.entries()).map(([exchange, count]) => ({ exchange, count })).sort((a, b) => b.count - a.count),
  };
};

export const getAvailableYears = (): number[] => {
  const years = new Set<number>();
  for (const i of allHistorical()) {
    const y = Number((i.listing_date ?? i.close_date ?? '').slice(0, 4));
    if (Number.isFinite(y) && y > 2000) years.add(y);
  }
  return Array.from(years).sort((a, b) => b - a);
};

export type IssueIngredient = {
  brlmName: string | null;
  lotSize: number | null;
  sharesOffered: number | null;
  impliedSize: number | null;
  faceValue: number | null;
  issuePrice: number | null;
};

export const extractIngredient = (issue: IssueDetail): IssueIngredient & { priceMin: number | null; priceMax: number | null } => {
  const priceMin = issue.pricing.price_band.min;
  const priceMax = issue.pricing.price_band.max ?? issue.pricing.issue_price;
  const sharesOffered = issue.issue_size.shares_offered;
  const impliedSize = issue.issue_size.amount ?? (sharesOffered && priceMax != null ? sharesOffered * priceMax : null);
  return {
    brlmName: null,
    lotSize: issue.pricing.lot_size_shares ?? null,
    sharesOffered,
    impliedSize,
    priceMin,
    priceMax,
    faceValue: issue.pricing.face_value,
    issuePrice: issue.pricing.issue_price,
  };
};

export const getRecentByType = (type: string, count: number): IssueSummary[] => {
  const matching = getAllRecentHistorical().filter((i) => normalizeIssueType(i.issue_type) === type);
  return matching.slice().sort(sortDateDesc).slice(0, count);
};

export const getCountByType = (type: string): number =>
  getAllRecentHistorical().filter((i) => normalizeIssueType(i.issue_type) === type).length;

export const getTopGainersAllTime = (count: number, opts?: { mainboardOnly?: boolean }): IssueSummary[] => {
  const filtered = allHistorical().filter((i) => {
    if (i.listing_day_gain == null) return false;
    if (opts?.mainboardOnly && (i.exchange_platform ?? '').includes('SME')) return false;
    return true;
  });
  return filtered.slice().sort((a, b) => (b.listing_day_gain ?? 0) - (a.listing_day_gain ?? 0)).slice(0, count);
};

export const getWorstListingsAllTime = (count: number, opts?: { mainboardOnly?: boolean }): IssueSummary[] => {
  const filtered = allHistorical().filter((i) => {
    if (i.listing_day_gain == null) return false;
    if (opts?.mainboardOnly && (i.exchange_platform ?? '').includes('SME')) return false;
    return true;
  });
  return filtered.slice().sort((a, b) => (a.listing_day_gain ?? 0) - (b.listing_day_gain ?? 0)).slice(0, count);
};

export type DistBin = { from: number; to: number; count: number };
export const getGainDistribution = (months: number, now: Date = new Date()): {
  bins: DistBin[];
  total: number;
  median: number | null;
  positive: number;
} => {
  const cutoff = new Date(now.getFullYear(), now.getMonth() - months, now.getDate());
  const cutoffIso = cutoff.toISOString().slice(0, 10);
  const gains: number[] = [];
  for (const i of allHistorical()) {
    if (i.listing_day_gain == null || !i.listing_date || i.listing_date < cutoffIso) continue;
    gains.push(i.listing_day_gain);
  }
  const edges = [-100, -50, -40, -30, -20, -10, 0, 10, 20, 30, 40, 50, 75, 100, 1000];
  const counts: number[] = new Array(edges.length - 1).fill(0);
  for (const g of gains) {
    for (let k = 0; k < counts.length; k++) {
      if (g >= edges[k] && g < edges[k + 1]) { counts[k]++; break; }
    }
  }
  return {
    bins: counts.map((count, k) => ({ from: edges[k], to: edges[k + 1], count })),
    total: gains.length,
    median: median(gains),
    positive: gains.filter((g) => g > 0).length,
  };
};

export const getMonthlyCounts = (months: number, now: Date = new Date()): { ym: string; count: number }[] => {
  const map = new Map<string, number>();
  const monthsArr: string[] = [];
  for (let i = months - 1; i >= 0; i--) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    const ym = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    monthsArr.push(ym);
    map.set(ym, 0);
  }
  for (const i of allHistorical()) {
    const ym = (i.listing_date ?? '').slice(0, 7);
    if (map.has(ym)) map.set(ym, (map.get(ym) ?? 0) + 1);
  }
  return monthsArr.map((ym) => ({ ym, count: map.get(ym) ?? 0 }));
};

export type DatahubAnnualRow = {
  issues_total_count?: number;
  public_issues_count?: number;
  ipo_count?: number;
  ipo_mainboard_count?: number;
  ipo_sme_count?: number;
  fpo_count?: number;
  rights_count?: number;
  qip_count?: number;
  preferential_count?: number;
  total_raised_cr?: number;
  public_issues_raised_cr?: number;
  ipo_raised_cr?: number;
  ipo_mainboard_raised_cr?: number;
  ipo_sme_raised_cr?: number;
  fpo_raised_cr?: number;
  rights_raised_cr?: number;
  qip_raised_cr?: number;
  preferential_raised_cr?: number;
};

export type DatahubFile = {
  generated_at: string;
  source: string;
  frequency: string;
  indicators: Record<string, { id: string; title: string; monthly: { date: string; value: number | null }[] }>;
  annual: Record<string, DatahubAnnualRow>;
};

let datahubCache: DatahubFile | null = null;
export const getDatahub = (): DatahubFile | null => {
  if (datahubCache) return datahubCache;
  try {
    const path = join(legacyDataRoot, 'datahub', 'capital_raising.json');
    if (!existsSync(path)) return null;
    datahubCache = JSON.parse(readFileSync(path, 'utf-8')) as DatahubFile;
    return datahubCache;
  } catch { return null; }
};

export const sumMonthly = (
  series: { date: string; value: number | null }[] | undefined,
  fromIso: string,
  toIso: string,
): number => {
  if (!series) return 0;
  let sum = 0;
  for (const o of series) {
    if (!o.date || o.value == null) continue;
    if (o.date < fromIso || o.date > toIso) continue;
    sum += Math.max(0, o.value);
  }
  return sum;
};

export const normCompany = (name: string): string =>
  name.toLowerCase()
    .replace(/\b(limited|ltd|pvt|private|inc|company|co)\.?\b/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();

export const dedupByCompany = <T extends { company_name: string }>(items: T[]): T[] => {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const i of items) {
    const k = normCompany(i.company_name);
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(i);
  }
  return out;
};

export const listCompanyIndexEntries = (): CompanyIndexEntry[] => companyEntries();
