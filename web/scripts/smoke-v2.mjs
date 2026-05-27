// Runtime smoke test for the v2 data layer — proves Node ingests data/site_v2
// and the formatters produce correct display values. Run: node scripts/smoke-v2.mjs
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = process.env.IPO_DATA_DIR?.trim()
  || path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../data/site_v2');
const read = (rel) => JSON.parse(fs.readFileSync(path.join(ROOT, rel), 'utf-8'));

// formatters (mirror src/lib/formatv2.ts)
const grp = (n) => n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const formatINR = (p) => p == null ? '—'
  : Math.abs(p/100) >= 1e7 ? `₹${grp(p/100/1e7)} Cr`
  : Math.abs(p/100) >= 1e5 ? `₹${grp(p/100/1e5)} L`
  : `₹${grp(p/100)}`;
const formatBps = (b) => b == null ? '—' : `${b>=0?'+':''}${(b/100).toFixed(2)}%`;
const formatX = (x) => x == null ? '—' : `${parseFloat(x).toFixed(2)}×`;

let pass = 0, fail = 0;
const check = (label, got, want) => {
  const ok = got === want;
  console.log(`  ${ok ? '✓' : '✗'} ${label}: ${got}${ok ? '' : `  (expected ${want})`}`);
  ok ? pass++ : fail++;
};

console.log('ROOT =', ROOT, '\n');
console.log('Formatter correctness (brief §3 worked examples):');
check('31500 paise', formatINR(31500), '₹315.00');
check('3000000000000 paise', formatINR(3000000000000), '₹3,000.00 Cr');
check('1455 bps', formatBps(1455), '+14.55%');
check('"191.9300"', formatX('191.9300'), '191.93×');

console.log('\nData access:');
const idx = read('issues/index.json');
console.log(`  ✓ issues/index.json → ${idx.items.length} items (dataset_version ${idx.dataset_version})`);
const open = read('issues/by-status/open.json');
console.log(`  ✓ by-status/open → ${open.items.length} open issues`);

const slug = open.items[0]?.slug ?? idx.items[0].slug;
const rec = read(`issues/by-slug/${slug}.json`);
console.log(`\nRendered detail for "${slug}" (${rec.identity.company_name}):`);
console.log('  status      :', rec.identity.status, '| board:', rec.identity.board_type ?? '—');
console.log('  price band  :', formatINR(rec.pricing?.price_band_lower_paise), '–', formatINR(rec.pricing?.price_band_upper_paise));
console.log('  issue size  :', formatINR(rec.pricing?.issue_size_paise));
console.log('  subscription:', formatX(rec.subscription?.overall_times_x));
console.log('  listing gain:', formatBps(rec.listing_performance?.listing_gain_bps));
console.log('  quality     :', rec.data_quality.state);

const pPath = path.join(ROOT, 'issues', slug, 'prospectus.json');
if (fs.existsSync(pPath)) {
  const pr = JSON.parse(fs.readFileSync(pPath, 'utf-8'));
  const cites = (JSON.stringify(pr).match(/"source_page"/g) || []).length;
  console.log(`  prospectus  : present, ${cites} page-citations; hero pitch:`,
    JSON.stringify(pr.hero?.headline_pitch?.value)?.slice(0, 70));
} else {
  console.log('  prospectus  : (none for this slug)');
}

console.log(`\n${fail === 0 ? 'ALL FORMATTER CHECKS PASSED' : fail + ' FAILED'} (${pass} ok)`);
process.exit(fail === 0 ? 0 : 1);
