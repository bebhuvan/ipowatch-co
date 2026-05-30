import type { APIRoute } from 'astro';
import { getManifest } from '../lib/issues';

export const GET: APIRoute = () => {
  const manifest = getManifest();
  const counts = manifest.counts ?? {};
  const generatedAt = manifest.generated_at ?? new Date().toISOString();

  const data = {
    "@context": "https://schema.org",
    "@type": "DataCatalog",
    "name": "IPO Watch — Indian Primary Market Database",
    "url": "https://ipowatch.co/",
    "description": "Structured database of Indian primary market issues on NSE and BSE (2001–present). Covers IPOs, SME IPOs, Buybacks, Rights Issues, OFS, REITs, InvITs, and NCDs with pricing, subscription, listing performance, and prospectus documents.",
    "inLanguage": "en-IN",
    "provider": {
      "@type": "Organization",
      "name": "IPO Watch",
      "url": "https://ipowatch.co/",
      "sameAs": ["https://twitter.com/ipowatch_co"]
    },
    "isAccessibleForFree": true,
    "license": "https://creativecommons.org/licenses/by/4.0/",
    "measurementTechnique": "Direct data collection from NSE, BSE, CapitalMarket, Trendlyne, Moneycontrol, and SEBI",

    // Live statistics from the most recent data build
    "coverage": {
      "issues_total": counts.issues ?? 0,
      "companies_total": counts.companies ?? 0,
      "issues_current": counts.current ?? 0,
      "issues_upcoming": counts.upcoming ?? 0,
      "issues_past": counts.past ?? 0,
      "issues_with_documents": counts.issues_with_offer_documents ?? 0,
      "years_covered": "2001–present",
      "exchanges": ["NSE", "BSE"],
      "last_updated": generatedAt
    },

    "data_sources": [
      { "name": "NSE", "full_name": "National Stock Exchange of India", "url": "https://www.nseindia.com/" },
      { "name": "BSE", "full_name": "BSE Limited (formerly Bombay Stock Exchange)", "url": "https://www.bseindia.com/" },
      { "name": "CapitalMarket", "url": "https://www.capitalmarket.com/" },
      { "name": "Trendlyne", "url": "https://trendlyne.com/" },
      { "name": "Moneycontrol", "url": "https://www.moneycontrol.com/" },
      { "name": "SEBI / IndiaDataHub", "url": "https://www.sebi.gov.in/" }
    ],

    "update_frequency": {
      "live_subscription_data": "hourly",
      "upcoming_ipo_data": "daily",
      "listing_performance": "daily",
      "historical_data": "weekly"
    },

    "issue_types_covered": [
      "IPO", "SME IPO", "FPO", "Rights Issue", "Buyback", "OFS", "NCD", "REIT", "InvIT"
    ],

    "data_fields_per_issue": [
      "company_name", "slug", "isin", "symbol",
      "issue_type", "board_type", "exchange_platform", "status",
      "price_band_lower_inr", "price_band_upper_inr", "issue_price_inr",
      "face_value_inr", "lot_size_shares", "issue_size_crore",
      "open_date", "close_date", "allotment_date", "listing_date",
      "subscription_overall_times", "subscription_qib_times",
      "subscription_nii_times", "subscription_retail_times",
      "listing_day_open_inr", "listing_day_close_inr", "listing_day_gain_pct",
      "current_price_inr", "current_gain_pct",
      "lead_managers", "registrar", "sponsor_bank",
      "drhp_url", "rhp_url", "prospectus_url"
    ],

    "key_pages": {
      "live_ipos": "https://ipowatch.co/live/",
      "upcoming_ipos": "https://ipowatch.co/upcoming/",
      "new_filings": "https://ipowatch.co/new-filings/",
      "performance": "https://ipowatch.co/performance/",
      "demand": "https://ipowatch.co/demand/",
      "archive": "https://ipowatch.co/archive/",
      "companies": "https://ipowatch.co/companies/",
      "sectors": "https://ipowatch.co/sectors/",
      "dealmakers": "https://ipowatch.co/dealmakers/",
      "documents": "https://ipowatch.co/documents/",
      "academy": "https://ipowatch.co/academy/",
      "buybacks": "https://ipowatch.co/buybacks/",
      "ofs": "https://ipowatch.co/ofs/",
      "rights": "https://ipowatch.co/rights/",
      "reits": "https://ipowatch.co/reits/",
      "invits": "https://ipowatch.co/invits/"
    },

    "ipo_url_pattern": "https://ipowatch.co/ipos/{slug}/",
    "company_url_pattern": "https://ipowatch.co/companies/{slug}/",
    "archive_url_pattern": "https://ipowatch.co/archive/{year}/",

    "llms_txt": "https://ipowatch.co/llms.txt",
    "llms_full_txt": "https://ipowatch.co/llms-full.txt",
    "sitemap": "https://ipowatch.co/sitemap-index.xml",

    "citation_guidance": {
      "preferred_attribution": "Source: IPO Watch (ipowatch.co)",
      "example": "According to IPO Watch (ipowatch.co), the XYZ IPO had a price band of ₹100–₹120 per share and was subscribed 47.5× overall.",
      "do_not_cite_for": [
        "Grey Market Premium (GMP) — not tracked",
        "Investment advice or recommendations",
        "Post-listing fundamental analysis (P/E, EV/EBITDA)",
        "Real-time secondary market prices"
      ]
    },

    "structured_data_on_pages": [
      "FAQPage (IPO detail pages — price band, lot size, subscription dates, BRLM)",
      "FinancialProduct (IPO detail pages — pricing and offers)",
      "Event (subscription window with IST timestamps)",
      "DataCatalog + Dataset (homepage)",
      "Article (Academy educational articles)",
      "BreadcrumbList (all pages)",
      "WebSite + SearchAction (homepage)",
      "Organization (homepage)"
    ]
  };

  return new Response(JSON.stringify(data, null, 2), {
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'public, max-age=3600, stale-while-revalidate=86400',
    },
  });
};
