// View helpers for rendering extracted financial statements. Plain TS (not
// Astro frontmatter), so regex literals etc. are safe here. Shared by the
// /report snapshot band and the /report/financials deep-dive page.

import type { Financials, FinancialStatement, FinancialRow } from './issues';

export type ChartDatum = { label: string; value: number | null };
export type Kpi = { label: string; value: string };

const DASHES = ['-', '—', '–'];
const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];

export function parseNum(v: string | null | undefined): number | null {
  if (v == null) return null;
  const t = String(v).trim();
  if (!t || DASHES.indexOf(t) !== -1) return null;
  const low = t.toLowerCase();
  if (low === 'na' || low === 'n.a.' || low === 'nil') return null;
  const neg = t.startsWith('(') && t.endsWith(')');
  const cleaned = t.replace(/[^0-9.]/g, '');
  if (!cleaned) return null;
  const n = Number(cleaned);
  if (Number.isNaN(n)) return null;
  return neg ? -n : n;
}

export function shortPeriod(label: string): string {
  const ym = /20\d{2}/.exec(label);
  const year = ym ? ym[0] : '';
  const yy = year ? year.slice(2) : '';
  const lower = label.toLowerCase();
  const isStub = lower.indexOf('period ended') !== -1 && !/31\s*march/.test(lower);
  if (isStub) {
    const mon = MONTHS.find((m) => lower.indexOf(m) !== -1);
    const monLabel = mon ? mon.charAt(0).toUpperCase() + mon.slice(1) : 'P.E.';
    return year ? monLabel + " '" + yy + '*' : monLabel + '*';
  }
  return year ? 'FY' + yy : label;
}

export function findRow(st: FinancialStatement | undefined, keywords: string[]): FinancialRow | undefined {
  const rows = st && st.rows ? st.rows.slice().reverse() : [];
  return rows.find((r) => {
    const lab = (r.label || '').toLowerCase();
    return keywords.some((k) => lab.indexOf(k) !== -1);
  });
}

function periodsOf(fin: Financials): string[] {
  return fin.statements.pnl.periods.length ? fin.statements.pnl.periods : fin.periods;
}

// Detect the multiplier to convert raw statement values to ₹ crore.
// Handles: ₹ in crore (1), ₹ in million (0.1), ₹ in lakhs (0.01),
//          Indian Rupees / raw (0.0000001 i.e. /1e7).
export function unitToCroreDivisor(unit: string | null | undefined): number {
  const u = (unit || '').toLowerCase();
  if (u.includes('crore') || u.includes('cr')) return 1;
  if (u.includes('million')) return 0.1;   // 1 million = 0.1 crore
  if (u.includes('lakh')) return 0.01;     // 1 lakh = 0.01 crore
  // "Indian Rupees" or bare rupees — raw paise sometimes, but usually rupees.
  // Divide by 1e7 to get crore from raw rupees.
  return 1 / 1e7;
}

// Chronological (oldest to newest) crore series from a statement row.
export function seriesCr(row: FinancialRow | undefined, periods: string[], divisor: number = 1 / 1e7): ChartDatum[] {
  if (!row) return [];
  const shortP = periods.map(shortPeriod);
  const out = row.values.map((v, i) => {
    const num = parseNum(v);
    return { label: shortP[i] || periods[i] || '', value: num == null ? null : num * divisor };
  });
  return out.reverse();
}

// Format a value that is already in crore units.
// ≥ 1 cr → "₹X.X cr"   < 1 cr → "₹XX L"  (always Indian units, never millions)
function crore(n: number | null): string {
  if (n == null) return '';
  if (Math.abs(n) >= 1) return '₹' + n.toLocaleString('en-IN', { maximumFractionDigits: 1 }) + ' cr';
  return '₹' + (n * 100).toLocaleString('en-IN', { maximumFractionDigits: 1 }) + ' L';
}

const REVENUE = ['revenue from operations'];
const PAT = ['profit after tax', 'profit for the', 'net profit', 'restated profit'];
const RONW = ['return on net worth', 'ronw'];

export type FinancialsSnapshot = {
  periods: string[];
  revenueSeries: ChartDatum[];
  patSeries: ChartDatum[];
  kpis: Kpi[];
};

export function buildSnapshot(fin: Financials): FinancialsSnapshot {
  const periods = periodsOf(fin);
  const divisor = unitToCroreDivisor(fin.currency_unit);
  const revenueRow = findRow(fin.statements.pnl, REVENUE);
  const patRow = findRow(fin.statements.pnl, PAT);
  const ronwRow = findRow(fin.statements.ratios, RONW);

  const revenue = revenueRow ? parseNum(revenueRow.values[0]) : null;
  const pat = patRow ? parseNum(patRow.values[0]) : null;
  const ronw = ronwRow ? (ronwRow.values[0] || '') : '';

  const kpis: Kpi[] = [];
  if (revenue != null) kpis.push({ label: 'Revenue', value: crore(revenue * divisor) });
  if (pat != null) kpis.push({ label: 'Net profit', value: crore(pat * divisor) });
  if (ronw) kpis.push({ label: 'Return on net worth', value: String(ronw) });

  return {
    periods,
    revenueSeries: seriesCr(revenueRow, periods, divisor),
    patSeries: seriesCr(patRow, periods, divisor),
    kpis,
  };
}
