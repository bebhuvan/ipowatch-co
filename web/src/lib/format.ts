// Formatting helpers — all numbers passed through here so the typography stays consistent.

const indianRupee = new Intl.NumberFormat('en-IN', {
  maximumFractionDigits: 2,
  minimumFractionDigits: 0,
});

const indianInteger = new Intl.NumberFormat('en-IN', {
  maximumFractionDigits: 0,
});

const englishDateLong = new Intl.DateTimeFormat('en-IN', {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
});

const englishDateShort = new Intl.DateTimeFormat('en-IN', {
  day: 'numeric',
  month: 'short',
});

const englishWeekday = new Intl.DateTimeFormat('en-IN', { weekday: 'short' });

const isoDateOnly = (text: string | null | undefined): Date | null => {
  if (!text) return null;
  const d = new Date(`${text.slice(0, 10)}T00:00:00`);
  return Number.isNaN(d.getTime()) ? null : d;
};

const isoDateTime = (text: string | null | undefined): Date | null => {
  if (!text) return null;
  const d = new Date(text);
  return Number.isNaN(d.getTime()) ? null : d;
};

export const formatRupee = (n: number | null | undefined): string => {
  if (n == null || Number.isNaN(n)) return '—';
  return `₹${indianRupee.format(n)}`;
};

export const formatRupeeRange = (a: number | null, b: number | null): string => {
  if (a == null && b == null) return '—';
  if (a == null) return formatRupee(b);
  if (b == null || a === b) return formatRupee(a);
  return `₹${indianRupee.format(a)} – ₹${indianRupee.format(b)}`;
};

export const formatLakhCr = (rupees: number | null | undefined): string => {
  if (rupees == null) return '—';
  if (rupees >= 1e7) return `₹${(rupees / 1e7).toFixed(2)} Cr`;
  if (rupees >= 1e5) return `₹${(rupees / 1e5).toFixed(2)} L`;
  return formatRupee(rupees);
};

export const formatTimes = (n: number | null | undefined): string => {
  if (n == null) return '—';
  return `${indianRupee.format(n)}×`;
};

export const formatInt = (n: number | null | undefined): string => {
  if (n == null) return '—';
  return indianInteger.format(n);
};

export const formatDateLong = (iso: string | null | undefined): string => {
  const d = isoDateOnly(iso);
  return d ? englishDateLong.format(d) : '—';
};

export const formatDateShort = (iso: string | null | undefined): string => {
  const d = isoDateOnly(iso);
  return d ? englishDateShort.format(d) : '—';
};

export const formatDateRange = (start: string | null, end: string | null): string => {
  const s = isoDateOnly(start);
  const e = isoDateOnly(end);
  if (!s && !e) return '—';
  if (s && !e) return englishDateLong.format(s);
  if (!s && e) return englishDateLong.format(e!);
  const sameYear = s!.getFullYear() === e!.getFullYear();
  const sameMonth = sameYear && s!.getMonth() === e!.getMonth();
  if (sameMonth) {
    return `${englishDateShort.format(s!)} – ${englishDateLong.format(e!)}`;
  }
  return `${englishDateShort.format(s!)} – ${englishDateLong.format(e!)}`;
};

export const formatTimeOfDay = (iso: string | null | undefined): string => {
  const d = isoDateTime(iso);
  if (!d) return '';
  const time = new Intl.DateTimeFormat('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Kolkata',
  }).format(d);
  return `${time} IST`;
};

export const weekdayOf = (iso: string | null | undefined): string => {
  const d = isoDateOnly(iso);
  return d ? englishWeekday.format(d) : '';
};

/* ── Company display names ──────────────────────────────────────────────
   Scraped names arrive SHOUTING ("KOSAMATTAM FINANCE LIMITED") and carry
   artifacts ("Ltd-$", trailing " - 1"). displayName() de-shouts all-caps
   names for editorial display while preserving acronyms (BCC, PH, PVV) and
   well-known listed tickers (ONGC, NTPC …). Mixed-case names are left as-is.
*/
const LEGAL_SUFFIX = new Set([
  'LTD', 'LIMITED', 'PVT', 'PRIVATE', 'CO', 'INC', 'CORP', 'LLP', 'PLC',
]);
// 4–5 letter all-caps names that title-casing would wrongly turn into words.
const KEEP_UPPER = new Set([
  'ONGC', 'NTPC', 'BHEL', 'GAIL', 'NHPC', 'SJVN', 'IRFC', 'RVNL', 'IRCTC',
  'BPCL', 'HPCL', 'NMDC', 'SAIL', 'MOIL', 'RITES', 'NBCC', 'HUDCO', 'IREDA',
  'BEML', 'MMTC', 'MTNL', 'BSNL', 'IDBI', 'HDFC', 'ICICI', 'IDFC', 'IIFL',
  'NHAI', 'IOCL', 'MRPL', 'CPCL', 'KIOCL', 'HFCL', 'CESC', 'JSW', 'UPL',
  'DLF', 'GMR', 'GVK', 'MRF', 'TCS', 'ITC', 'HCL', 'L&T',
]);
const CONNECTORS = new Set(['of', 'and', 'the', 'for', 'in', 'on', 'to', 'a', 'an']);

const capWord = (w: string): string => w.charAt(0) + w.slice(1).toLowerCase();
const isLegalSuffix = (t: string): boolean => LEGAL_SUFFIX.has(t.toUpperCase().replace(/\.$/, ''));

export const displayName = (
  name: string | null | undefined,
  opts: { dropSuffix?: boolean } = {},
): string => {
  if (!name) return '';
  let s = name
    .replace(/[-–]\s*\$\s*$/, '')   // trailing "-$" scraper marker
    .replace(/\s*[-–]\s*\d+\s*$/, '') // trailing " - 1" dedupe marker
    .replace(/\s{2,}/g, ' ')
    .trim();

  let tokens = s.split(' ').filter(Boolean);

  if (opts.dropSuffix) {
    while (tokens.length > 1 && isLegalSuffix(tokens[tokens.length - 1])) tokens.pop();
  }

  const allCaps = /[A-Z]/.test(s) && !/[a-z]/.test(s);
  if (allCaps) {
    tokens = tokens.map((tok, i) => {
      const dot = tok.endsWith('.') ? '.' : '';
      const up = tok.toUpperCase().replace(/\.$/, '');
      if (i > 0 && CONNECTORS.has(up.toLowerCase())) return up.toLowerCase() + dot;
      if (isLegalSuffix(tok)) return capWord(up) + dot;   // LTD → Ltd
      if (KEEP_UPPER.has(up)) return up + dot;            // ONGC stays
      if (up.length <= 3) return up + dot;                // BCC, PH, & stay
      return capWord(up) + dot;                           // FUBA → Fuba
    });
  }

  return tokens.join(' ');
};

export const daysFromNow = (iso: string | null, now: Date = new Date()): number | null => {
  const d = isoDateOnly(iso);
  if (!d) return null;
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const diff = Math.round((d.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
  return diff;
};
