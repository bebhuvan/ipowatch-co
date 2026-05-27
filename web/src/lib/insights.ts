import { extractIngredient, getDetailBySlug, listAllBySlugFiles, listCompanyIndexEntries, normalizeIssueType, type IssueDetail } from './issues';
import { performanceRows, type PerfRow } from './performance';

type RawPerformance = PerfRow & {
  listing_day_close: number | null;
  listing_day_open: number | null;
  gain_loss: number | null;
  stock_url?: string | null;
};

export type PerformanceInsight = RawPerformance & {
  day_one_return: number | null;
  current_return: number | null;
  issue_price: number | null;
  listing_day_close: number | null;
  current_price: number | null;
  stock_url?: string | null;
};

export type DemandInsight = {
  issue: IssueDetail;
  finalTimes: number | null;
  qibTimes: number | null;
  niiTimes: number | null;
  retailTimes: number | null;
  bidRows: { price: number; quantity: number; processAt: string | null }[];
  anchorNotices: { subject: string | null; url: string | null; date: string | null }[];
};

export type PartyInsight = {
  name: string;
  kind: 'brlm' | 'registrar';
  issueCount: number;
  activeCount: number;
  impliedRaise: number;
  avgDayOneReturn: number | null;
  medianSubscription: number | null;
  sampleIssues: IssueDetail[];
};

export type CompanyIndexEntry = {
  id: string;
  name: string;
  slug: string;
  url_path: string;
  issue_count: number;
  latest_issue_date: string | null;
};

export type CompanyEvent = {
  issue: IssueDetail;
  eventDate: string | null;
  amount: number | null;
  dayOneReturn: number | null;
  currentReturn: number | null;
  subscriptionTimes: number | null;
  brlms: string[];
  registrars: string[];
};

export type SectorInsight = {
  industry: string;
  issueCount: number;
  sampleIssues: IssueDetail[];
  avgDayOneReturn: number | null;
  avgCurrentReturn: number | null;
};

export type DocumentTrail = {
  issue: IssueDetail;
  entries: {
    label: string;
    url: string;
    date: string | null;
    source: string;
  }[];
};

let detailCache: IssueDetail[] | null = null;

const details = (): IssueDetail[] => {
  if (detailCache) return detailCache;
  detailCache = listAllBySlugFiles()
    .map((file) => getDetailBySlug(file.replace(/\.json$/, '')))
    .filter((issue): issue is IssueDetail => issue !== null);
  return detailCache;
};

export const percentChange = (from: number | null | undefined, to: number | null | undefined): number | null => {
  if (from == null || to == null || !Number.isFinite(from) || !Number.isFinite(to) || from === 0) return null;
  return ((to - from) / from) * 100;
};

const median = (values: number[]): number | null => {
  if (!values.length) return null;
  const sorted = values.slice().sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
};

const mean = (values: number[]): number | null => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;

const normalizedName = (s: string | null | undefined): string => (s ?? '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();

const parseNumber = (value: unknown): number | null => {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value !== 'string') return null;
  const match = value.replace(/,/g, '').match(/\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : null;
};

const parsePriceRange = (text: string | null | undefined): { min: number | null; max: number | null } => {
  if (!text) return { min: null, max: null };
  const nums = text.replace(/,/g, '').match(/\d+(?:\.\d+)?/g)?.map(Number).filter(Number.isFinite) ?? [];
  if (!nums.length) return { min: null, max: null };
  if (nums.length === 1) return { min: nums[0], max: nums[0] };
  return { min: Math.min(nums[0], nums[1]), max: Math.max(nums[0], nums[1]) };
};

const nseLookup = (issue: IssueDetail, key: string): string | null => {
  const list = issue.exchange_details?.nse?.issue_detail?.data?.issueInfo?.dataList;
  if (!Array.isArray(list)) return null;
  const row = list.find((r: any) => (r?.title ?? '').trim().toLowerCase() === key.trim().toLowerCase());
  return row?.value ?? null;
};

const bseMeta = (issue: IssueDetail): Record<string, any> | null => issue.exchange_details?.bse?.issue_detail?.data?.IPONO_0?.[0] ?? null;

const cleanPartyName = (name: string): string => {
  const cleaned = name
    .replace(/\bPvt\.?\b/gi, 'Private')
    .replace(/\bLtd\.?\b/gi, 'Limited')
    .replace(/\bServies\b/gi, 'Services')
    .replace(/\s+/g, ' ')
    .trim();
  return cleaned.replace(/\w\S*/g, (word) => {
    if (/^(NSE|BSE|HDFC|SBI|ICICI|IDBI|SMC|SME|IFCI|PNB|YES)$/i.test(word)) return word.toUpperCase();
    return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
  });
};

const partyNames = (raw: string | null | undefined): string[] => {
  if (!raw) return [];
  const head = raw.split('^')[0] ?? raw;
  return head
    .split(/#|;|,|\band\b/gi)
    .map((p) => p.trim())
    .filter(Boolean)
    .map(cleanPartyName);
};

export const brlmNames = (issue: IssueDetail): string[] => {
  const bse = bseMeta(issue);
  return partyNames(bse?.Book_Running_Lead_Manager ?? nseLookup(issue, 'Book Running Lead Managers'));
};

export const registrarNames = (issue: IssueDetail): string[] => {
  const bse = bseMeta(issue);
  return partyNames(bse?.Registrar ?? nseLookup(issue, 'Name of the Registrar'));
};

export const issuePriceMax = (issue: IssueDetail): number | null => {
  const ing = extractIngredient(issue);
  if (ing.priceMax != null) return ing.priceMax;
  const bse = bseMeta(issue);
  const bseRange = parsePriceRange(bse?.Price_Band ?? null);
  return bseRange.max ?? issue.pricing.issue_price ?? null;
};

export const impliedRaise = (issue: IssueDetail): number | null => {
  const ing = extractIngredient(issue);
  if (ing.impliedSize != null) return ing.impliedSize;
  const bse = bseMeta(issue);
  const shares = issue.issue_size.shares_offered ?? parseNumber(bse?.Issue_Size_No_of_shares);
  const price = issuePriceMax(issue);
  return shares != null && price != null ? shares * price : null;
};

const detailScore = (issue: IssueDetail): number => {
  let score = 0;
  if (issue.timeline.open_date) score += 3;
  if (issue.timeline.listing_date) score += 3;
  if (issue.pricing.issue_price != null) score += 2;
  if (issue.listing_performance.current_price != null) score += 2;
  if (issue.subscription.times != null) score += 1;
  score += Math.min(3, issue.documents.length);
  score += issue.sources.length * 0.1;
  return score;
};

export const listPerformanceInsights = (limit?: number): PerformanceInsight[] => {
  const unique = new Map<string, PerformanceInsight>();
  for (const row of performanceRows().map((r) => ({
    ...r,
    listing_day_close: r.listing_close,
    listing_day_open: r.listing_open,
    gain_loss: r.current_return,
    stock_url: r.symbol ? `https://finance.yahoo.com/quote/${encodeURIComponent(r.symbol)}.NS` : null,
  })) as RawPerformance[]) {
    if (normalizeIssueType(row.issue_type) !== 'IPO') continue;
    if (row.issue_price == null || (row.listing_day_close == null && row.current_price == null)) continue;
    const enriched: PerformanceInsight = {
      ...row,
      day_one_return: percentChange(row.issue_price, row.listing_day_close),
      current_return: percentChange(row.issue_price, row.current_price),
    };
    const key = [
      normalizedName(row.company_name),
      row.listing_date ?? row.close_date ?? '',
      row.issue_price,
    ].join('|');
    const current = unique.get(key);
    if (!current) {
      unique.set(key, enriched);
      continue;
    }
    const score = (enriched.current_price != null ? 2 : 0) + (enriched.stock_url ? 1 : 0) + (enriched.slug.includes('undated') ? 0 : 1);
    const currentScore = (current.current_price != null ? 2 : 0) + (current.stock_url ? 1 : 0) + (current.slug.includes('undated') ? 0 : 1);
    if (score > currentScore) unique.set(key, enriched);
  }
  const rows = Array.from(unique.values()).sort((a, b) => (b.listing_date ?? '').localeCompare(a.listing_date ?? ''));
  return limit ? rows.slice(0, limit) : rows;
};

export const listYearReturnRows = (year: number, limit = 12): PerformanceInsight[] => (
  listPerformanceInsights()
    .filter((row) => (row.listing_date ?? row.close_date ?? '').startsWith(String(year)))
    .sort((a, b) => Math.abs(b.current_return ?? b.day_one_return ?? 0) - Math.abs(a.current_return ?? a.day_one_return ?? 0))
    .slice(0, limit)
);

const lastTrajectory = (issue: IssueDetail) => {
  const trajectory = issue.subscription.trajectory ?? [];
  return trajectory.length ? trajectory[trajectory.length - 1] : null;
};

export const listDemandInsights = (): DemandInsight[] => details()
  .map((issue) => {
    const last = lastTrajectory(issue);
    const bseRows = issue.exchange_details?.bse?.issue_detail?.data?.IPONO_2 ?? [];
    const bidRows = Array.isArray(bseRows)
      ? bseRows.map((row: any) => ({
        price: Number(row.Price ?? row.Price1),
        quantity: Number(row.Quantity),
        processAt: row.process_dttm ?? null,
      })).filter((row) => Number.isFinite(row.price) && Number.isFinite(row.quantity))
      : [];
    const notices = issue.exchange_details?.bse?.issue_detail?.data?.IPONO_4 ?? [];
    const anchorNotices = Array.isArray(notices)
      ? notices.map((row: any) => ({ subject: row.SUBJECT ?? null, url: row.FILENAME ?? null, date: row.NOTICE_DATE ?? null })).filter((row) => row.url || row.subject)
      : [];
    return {
      issue,
      finalTimes: last?.total?.times ?? issue.subscription.times ?? null,
      qibTimes: last?.categories?.qib?.times ?? null,
      niiTimes: last?.categories?.nii?.times ?? null,
      retailTimes: last?.categories?.retail?.times ?? null,
      bidRows,
      anchorNotices,
    };
  })
  .filter((row) => (row.issue.subscription.trajectory?.length ?? 0) > 0 || row.bidRows.length > 0)
  .sort((a, b) => (b.finalTimes ?? -1) - (a.finalTimes ?? -1));

export const listPartyInsights = (kind: 'brlm' | 'registrar'): PartyInsight[] => {
  const map = new Map<string, { issues: IssueDetail[]; amount: number; returns: number[]; subs: number[]; active: number }>();
  for (const issue of details()) {
    const names = kind === 'brlm' ? brlmNames(issue) : registrarNames(issue);
    if (!names.length) continue;
    const amount = impliedRaise(issue) ?? 0;
    const dayOne = percentChange(issue.pricing.issue_price, issue.listing_performance.listing_day_close);
    const sub = lastTrajectory(issue)?.total?.times ?? issue.subscription.times;
    for (const name of names) {
      const bucket = map.get(name) ?? { issues: [], amount: 0, returns: [], subs: [], active: 0 };
      bucket.issues.push(issue);
      bucket.amount += amount;
      if (dayOne != null) bucket.returns.push(dayOne);
      if (sub != null) bucket.subs.push(sub);
      if (issue.classification.status === 'current' || issue.classification.status === 'upcoming') bucket.active += 1;
      map.set(name, bucket);
    }
  }
  return Array.from(map.entries()).map(([name, bucket]) => ({
    name,
    kind,
    issueCount: bucket.issues.length,
    activeCount: bucket.active,
    impliedRaise: bucket.amount,
    avgDayOneReturn: mean(bucket.returns),
    medianSubscription: median(bucket.subs),
    sampleIssues: bucket.issues.sort((a, b) => (b.timeline.open_date ?? b.timeline.listing_date ?? '').localeCompare(a.timeline.open_date ?? a.timeline.listing_date ?? '')).slice(0, 4),
  })).sort((a, b) => b.issueCount - a.issueCount || b.impliedRaise - a.impliedRaise || a.name.localeCompare(b.name));
};

export const listCompaniesWithMultipleIssues = (): CompanyIndexEntry[] => (
  (listCompanyIndexEntries()
    .filter((company) => company.issue_count > 1)
    .sort((a, b) => b.issue_count - a.issue_count || a.name.localeCompare(b.name))
  )
);

export const getCompanyEntry = (slug: string): CompanyIndexEntry | null => (
  listCompanyIndexEntries().find((company) => company.slug === slug) ?? null
);

export const companyEvents = (slug: string): CompanyEvent[] => {
  const rows = details().filter((issue) => issue.company.slug === slug);
  const hasDatedType = new Set(rows.filter((issue) => issue.timeline.open_date || issue.timeline.listing_date || issue.timeline.close_date).map((issue) => normalizeIssueType(issue.classification.issue_type)));
  const unique = new Map<string, IssueDetail>();
  for (const issue of rows) {
    const type = normalizeIssueType(issue.classification.issue_type);
    const eventDate = issue.timeline.open_date ?? issue.timeline.listing_date ?? issue.timeline.close_date;
    if (!eventDate && hasDatedType.has(type)) continue;
    const key = [type, issue.classification.security_type ?? '', eventDate ?? normalizedName(issue.title)].join('|');
    const current = unique.get(key);
    if (!current || detailScore(issue) > detailScore(current)) unique.set(key, issue);
  }
  return Array.from(unique.values()).map((issue) => ({
    issue,
    eventDate: issue.timeline.open_date ?? issue.timeline.listing_date ?? issue.timeline.close_date,
    amount: impliedRaise(issue),
    dayOneReturn: percentChange(issue.pricing.issue_price, issue.listing_performance.listing_day_close),
    currentReturn: percentChange(issue.pricing.issue_price, issue.listing_performance.current_price),
    subscriptionTimes: lastTrajectory(issue)?.total?.times ?? issue.subscription.times,
    brlms: brlmNames(issue),
    registrars: registrarNames(issue),
  })).sort((a, b) => (a.eventDate ?? '9999').localeCompare(b.eventDate ?? '9999'));
};

const industryFor = (issue: IssueDetail): string | null => {
  const rows = issue.exchange_details?.nse?.offer_document_detail?.data?.data?.InPrincipal;
  if (!Array.isArray(rows)) return null;
  return rows.find((row: any) => row?.industry)?.industry ?? null;
};

export const listSectorInsights = (): { rows: SectorInsight[]; coveredIssues: number; totalIssues: number } => {
  const map = new Map<string, IssueDetail[]>();
  const total = details().filter((issue) => normalizeIssueType(issue.classification.issue_type) === 'IPO').length;
  for (const issue of details()) {
    const industry = industryFor(issue);
    if (!industry) continue;
    const bucket = map.get(industry) ?? [];
    bucket.push(issue);
    map.set(industry, bucket);
  }
  const rows = Array.from(map.entries()).map(([industry, sampleIssues]) => {
    const dayOnes = sampleIssues.map((issue) => percentChange(issue.pricing.issue_price, issue.listing_performance.listing_day_close)).filter((v): v is number => v != null);
    const currents = sampleIssues.map((issue) => percentChange(issue.pricing.issue_price, issue.listing_performance.current_price)).filter((v): v is number => v != null);
    return {
      industry,
      issueCount: sampleIssues.length,
      sampleIssues,
      avgDayOneReturn: mean(dayOnes),
      avgCurrentReturn: mean(currents),
    };
  }).sort((a, b) => b.issueCount - a.issueCount || a.industry.localeCompare(b.industry));
  return { rows, coveredIssues: rows.reduce((sum, row) => sum + row.issueCount, 0), totalIssues: total };
};

const addEntry = (entries: DocumentTrail['entries'], label: string, url: string | null | undefined, date: string | null, source: string) => {
  if (!url) return;
  if (entries.some((entry) => entry.url === url)) return;
  entries.push({ label, url, date, source });
};

export const listDocumentTrails = (limit = 80): DocumentTrail[] => {
  const trails: DocumentTrail[] = [];
  for (const issue of details()) {
    const entries: DocumentTrail['entries'] = [];
    for (const doc of issue.documents ?? []) {
      addEntry(entries, doc.type ?? 'Document', doc.url, doc.date ?? doc.source_date ?? null, 'normalized');
    }
    const bse = bseMeta(issue);
    addEntry(entries, 'Price band advertisement', bse?.Price_Band_Advertisement, null, 'bse');
    addEntry(entries, 'Prospectus', bse?.Prospectus_GID, null, 'bse');
    addEntry(entries, 'Corrigendum', bse?.Corrigendum, null, 'bse');
    const notices = issue.exchange_details?.bse?.issue_detail?.data?.IPONO_4 ?? [];
    if (Array.isArray(notices)) {
      for (const notice of notices) addEntry(entries, notice.SUBJECT ?? 'Exchange notice', notice.FILENAME, notice.NOTICE_DATE ?? null, 'bse');
    }
    const nseInfo = issue.exchange_details?.nse?.issue_detail?.data?.issueInfo?.dataList ?? [];
    if (Array.isArray(nseInfo)) {
      for (const row of nseInfo) {
        const value = row?.value;
        if (typeof value !== 'string' || !value.startsWith('http')) continue;
        addEntry(entries, row.title ?? 'NSE document', value, null, 'nse');
      }
    }
    if (entries.length) {
      entries.sort((a, b) => (a.date ?? '9999').localeCompare(b.date ?? '9999'));
      trails.push({ issue, entries });
    }
  }
  return trails
    .sort((a, b) => {
      const ad = a.issue.timeline.open_date ?? a.issue.timeline.listing_date ?? a.entries[0]?.date ?? '';
      const bd = b.issue.timeline.open_date ?? b.issue.timeline.listing_date ?? b.entries[0]?.date ?? '';
      return bd.localeCompare(ad);
    })
    .slice(0, limit);
};
