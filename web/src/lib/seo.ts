import type { IssueDetail } from './issues';

const SITE_URL = 'https://ipowatch.co';

export function getWebsiteSchema() {
  return {
    "@context": "https://schema.org",
    "@type": "WebSite",
    "name": "IPO Watch",
    "alternateName": "ipowatch.co",
    "url": `${SITE_URL}/`,
    "description": "Indian equity IPO and primary market data — NSE and BSE listings from 2001 to present with subscription data, pricing, documents, and performance metrics.",
    "inLanguage": "en-IN",
    "potentialAction": {
      "@type": "SearchAction",
      "target": {
        "@type": "EntryPoint",
        "urlTemplate": `${SITE_URL}/search/?q={search_term_string}`
      },
      "query-input": "required name=search_term_string"
    }
  };
}

export function getOrganizationSchema() {
  return {
    "@context": "https://schema.org",
    "@type": "Organization",
    "name": "IPO Watch",
    "alternateName": ["ipowatch.co", "IPO Watch India"],
    "url": `${SITE_URL}/`,
    "logo": {
      "@type": "ImageObject",
      "url": `${SITE_URL}/logo.svg`,
      "contentUrl": `${SITE_URL}/logo.svg`
    },
    "sameAs": [
      "https://twitter.com/ipowatch_co"
    ],
    "areaServed": {
      "@type": "Country",
      "name": "India",
      "sameAs": "https://en.wikipedia.org/wiki/India"
    },
    "knowsAbout": [
      "Indian Initial Public Offerings",
      "NSE and BSE primary market listings",
      "IPO subscription and demand data",
      "IPO price bands and lot sizes",
      "SEBI ICDR regulations",
      "Book running lead managers",
      "IPO listing day performance",
      "SME IPO listings",
      "Rights issues and Buybacks",
      "Offers for Sale (OFS)",
      "Real Estate Investment Trusts (REITs)",
      "Infrastructure Investment Trusts (InvITs)",
      "ASBA and UPI IPO application process",
      "Indian primary capital market"
    ],
    "description": "IPO Watch is the primary source for Indian equity IPO and primary market data, covering NSE and BSE listings from 2001 to present with subscription data, pricing, documents, and performance metrics.",
    "contactPoint": {
      "@type": "ContactPoint",
      "contactType": "customer service",
      "areaServed": "IN",
      "availableLanguage": "English"
    },
    "foundingDate": "2024",
    "location": {
      "@type": "PostalAddress",
      "addressCountry": "IN"
    }
  };
}

export function getBreadcrumbSchema(items: { name: string; url: string }[]) {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": items.map((item, index) => ({
      "@type": "ListItem",
      "position": index + 1,
      "name": item.name,
      "item": item.url.startsWith('http') ? item.url : `${SITE_URL}${item.url}`
    }))
  };
}

// DataCatalog + Dataset schema for the homepage — tells LLMs and Google this is a structured data authority
export function getDataCatalogSchema(counts: { issues: number; companies: number }) {
  return {
    "@context": "https://schema.org",
    "@type": "DataCatalog",
    "name": "IPO Watch — Indian Primary Market Database",
    "description": `Structured database of ${counts.issues.toLocaleString('en-IN')} Indian primary market issues across ${counts.companies.toLocaleString('en-IN')} companies. Covers IPOs, SME IPOs, Buybacks, Rights Issues, OFS, REITs, and InvITs listed on NSE and BSE from 2001 to present.`,
    "url": `${SITE_URL}/`,
    "provider": {
      "@type": "Organization",
      "name": "IPO Watch",
      "url": `${SITE_URL}/`
    },
    "license": "https://creativecommons.org/licenses/by/4.0/",
    "measurementTechnique": "Direct data collection from NSE, BSE, CapitalMarket, Trendlyne, Moneycontrol, and SEBI feeds",
    "isAccessibleForFree": true,
    "dataset": [
      {
        "@type": "Dataset",
        "name": "Indian IPO and Primary Market Issues",
        "description": `${counts.issues.toLocaleString('en-IN')} primary market issues on NSE and BSE (2001–present) with pricing, subscription data, listing performance, and prospectus documents.`,
        "url": `${SITE_URL}/archive/`,
        "temporalCoverage": "2001/..",
        "spatialCoverage": { "@type": "Country", "name": "India" },
        "variableMeasured": [
          "IPO Price Band (INR)",
          "Issue Size (INR Crore)",
          "Subscription Times by Investor Category (QIB, NII, Retail)",
          "Listing Day Gain/Loss (Percentage)",
          "Current Market Price (INR)",
          "Lot Size (Shares)",
          "Minimum Retail Investment (INR)"
        ],
        "creator": { "@type": "Organization", "name": "IPO Watch", "url": `${SITE_URL}/` },
        "isAccessibleForFree": true,
        "inLanguage": "en-IN",
        "about": {
          "@type": "Thing",
          "name": "Indian Primary Capital Market",
          "sameAs": "https://en.wikipedia.org/wiki/Initial_public_offering"
        }
      },
      {
        "@type": "Dataset",
        "name": "Indian IPO Listing Performance",
        "description": "Listing-day opening price, closing price, and percentage gain/loss for Indian IPOs listed on NSE and BSE.",
        "url": `${SITE_URL}/performance/`,
        "spatialCoverage": { "@type": "Country", "name": "India" },
        "variableMeasured": [
          "Listing Day Open Price (INR)",
          "Listing Day Close Price (INR)",
          "Listing Day Gain Percentage",
          "Current Market Price (INR)"
        ],
        "isAccessibleForFree": true,
        "inLanguage": "en-IN"
      }
    ]
  };
}

// FAQPage schema for IPO detail pages — directly feeds Google AI Overviews and LLM responses
export function getIPOFAQSchema(issue: IssueDetail): any | null {
  const company = issue.company.name;
  const issueType = issue.classification.issue_type ?? 'IPO';
  const exchange = issue.classification.exchange_platform;
  const name = `${company} ${issueType}`;

  const fmtINR = (v: number | null): string | null => {
    if (v == null) return null;
    return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(v);
  };

  const minPrice = issue.pricing.price_band.min;
  const maxPrice = issue.pricing.price_band.max;
  const issuePrice = issue.pricing.issue_price;
  const lotSize = issue.pricing.lot_size_shares ?? null;
  const subscribed = issue.subscription.trajectory.length
    ? (issue.subscription.trajectory[issue.subscription.trajectory.length - 1].total?.times ?? issue.subscription.times)
    : issue.subscription.times;

  type QA = { question: string; answer: string };
  const qas: QA[] = [];

  const push = (question: string, answer: string) => qas.push({ question, answer });

  // Price band / issue price
  if (minPrice != null && maxPrice != null) {
    push(
      `What is the price band for ${name}?`,
      `The price band for ${name} is ${fmtINR(minPrice)} to ${fmtINR(maxPrice)} per share.`
    );
  } else if (issuePrice != null) {
    push(
      `What is the issue price for ${name}?`,
      `The issue price for ${name} is ${fmtINR(issuePrice)} per share.`
    );
  } else if (maxPrice != null) {
    push(
      `What is the offer price for ${name}?`,
      `The offer price for ${name} is up to ${fmtINR(maxPrice)} per share.`
    );
  }

  // Lot size and minimum investment
  if (lotSize != null && maxPrice != null) {
    const minInv = lotSize * maxPrice;
    push(
      `What is the minimum investment for ${name}?`,
      `The minimum retail investment for ${name} is ${fmtINR(minInv)}, which buys one lot of ${lotSize} shares at the upper price band of ${fmtINR(maxPrice)} per share.`
    );
    push(
      `What is the lot size for ${name}?`,
      `The lot size for ${name} is ${lotSize} shares. Retail investors must apply for at least one lot.`
    );
  }

  // Subscription dates
  if (issue.timeline.open_date && issue.timeline.close_date) {
    const openFmt = new Date(issue.timeline.open_date).toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' });
    const closeFmt = new Date(issue.timeline.close_date).toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' });
    push(
      `When does the ${name} open and close for subscription?`,
      `The ${name} subscription opens on ${openFmt} and closes on ${closeFmt}. Bidding window is 10:00 AM to 5:00 PM IST on each subscription day. Applications can be submitted through ASBA or UPI via registered brokers.`
    );
  }

  // Listing date
  if (issue.timeline.listing_date) {
    const listingFmt = new Date(issue.timeline.listing_date).toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' });
    push(
      `When does ${name} list on the exchange?`,
      `${company} shares are scheduled to list on ${exchange || 'NSE and BSE'} on ${listingFmt}.`
    );
  }

  // Subscription status
  if (subscribed != null && subscribed > 0) {
    const times = subscribed.toFixed(2);
    push(
      `How many times was the ${name} subscribed?`,
      `The ${name} was subscribed ${times}× overall. This means total bids received were ${times} times the number of shares available. ${subscribed >= 10 ? 'Strong oversubscription typically indicates high investor demand.' : ''}`
    );
  }

  // Listing day performance
  if (issue.listing_performance.listing_day_gain != null) {
    const gain = issue.listing_performance.listing_day_gain;
    const listingClose = issue.listing_performance.listing_day_close;
    const direction = gain >= 0 ? `gained ${gain.toFixed(1)}%` : `declined ${Math.abs(gain).toFixed(1)}%`;
    const closeStr = listingClose != null ? `, closing at ${fmtINR(listingClose)} per share` : '';
    push(
      `What was the ${name} listing day return?`,
      `${company} shares ${direction} on listing day${closeStr} compared to the issue price.`
    );
  }

  // Exchange
  if (exchange) {
    push(
      `On which exchange is ${name} listed?`,
      `${company} is listed on ${exchange}. After listing, shares can be bought and sold on the exchange through a regular demat and trading account.`
    );
  }

  // Lead managers
  if (issue.parties.lead_managers.length > 0) {
    const brlms = issue.parties.lead_managers.join(', ');
    push(
      `Who is the book running lead manager (BRLM) for ${name}?`,
      `The book running lead manager(s) for ${name} ${issue.parties.lead_managers.length === 1 ? 'is' : 'are'}: ${brlms}. The BRLM manages the IPO process, investor outreach, and anchor book building.`
    );
  }

  // Issue size
  if (issue.issue_size.text) {
    const sizeText = String(issue.issue_size.text).replace(/^₹\s*/, '');
    push(
      `What is the issue size for ${name}?`,
      `The total issue size for ${name} is approximately ₹${sizeText} crore.`
    );
  }

  if (qas.length === 0) return null;

  return {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    "mainEntity": qas.map(({ question, answer }) => ({
      "@type": "Question",
      "name": question,
      "acceptedAnswer": { "@type": "Answer", "text": answer }
    }))
  };
}

// Article schema for Academy content — makes educational articles citable by LLMs
export function getArticleSchema(opts: {
  title: string;
  description: string;
  publishedAt: string;
  url: string;
  section: string;
  readingTimeMinutes?: number;
}) {
  const sectionLabels: Record<string, string> = {
    foundations: 'IPO Foundations',
    mechanics: 'IPO Mechanics',
    regulation: 'SEBI Regulation',
    glossary: 'IPO Glossary',
    timeline: 'Market Timeline',
  };
  return {
    "@context": "https://schema.org",
    "@type": "Article",
    "headline": opts.title,
    "description": opts.description,
    "datePublished": opts.publishedAt,
    "dateModified": opts.publishedAt,
    "url": opts.url,
    "mainEntityOfPage": { "@type": "WebPage", "@id": opts.url },
    "author": {
      "@type": "Organization",
      "name": "IPO Watch",
      "url": `${SITE_URL}/`
    },
    "publisher": {
      "@type": "Organization",
      "name": "IPO Watch",
      "url": `${SITE_URL}/`,
      "logo": { "@type": "ImageObject", "url": `${SITE_URL}/logo.svg` }
    },
    "about": {
      "@type": "Thing",
      "name": sectionLabels[opts.section] ?? "Indian IPO Market",
      "sameAs": "https://en.wikipedia.org/wiki/Initial_public_offering"
    },
    "isPartOf": {
      "@type": "WebSite",
      "name": "IPO Watch Academy",
      "url": `${SITE_URL}/academy/`
    },
    "inLanguage": "en-IN",
    "audience": { "@type": "Audience", "audienceType": "Indian retail investors and finance professionals" },
    ...(opts.readingTimeMinutes ? { "timeRequired": `PT${opts.readingTimeMinutes}M` } : {})
  };
}

// ItemList schema for listing pages — helps LLMs enumerate live/upcoming IPOs
export function getItemListSchema(
  items: { name: string; url: string; description?: string }[],
  listName: string,
  listUrl: string
) {
  return {
    "@context": "https://schema.org",
    "@type": "ItemList",
    "name": listName,
    "url": `${SITE_URL}${listUrl}`,
    "numberOfItems": items.length,
    "itemListElement": items.map((item, index) => ({
      "@type": "ListItem",
      "position": index + 1,
      "name": item.name,
      "url": item.url.startsWith('http') ? item.url : `${SITE_URL}${item.url}`,
      ...(item.description ? { "description": item.description } : {})
    }))
  };
}

export function getIPOSchemas(issue: IssueDetail) {
  const schemas: any[] = [];
  const company = issue.company.name;
  const issueType = issue.classification.issue_type || 'IPO';

  // 1. Breadcrumbs
  schemas.push(getBreadcrumbSchema([
    { name: "Home", url: "/" },
    { name: "IPOs", url: "/ipos/" },
    { name: company, url: `/ipos/${issue.slug}/` }
  ]));

  // 2. Financial Product — enriched with additionalProperty for key facts
  const minPrice = issue.pricing.price_band.min;
  const maxPrice = issue.pricing.price_band.max;
  const issuePrice = issue.pricing.issue_price;
  const lotSize = issue.pricing.lot_size_shares ?? null;

  const offers: any = { "@type": "Offer", "priceCurrency": "INR" };
  if (issuePrice != null) {
    offers.price = issuePrice;
  } else if (minPrice != null && maxPrice != null) {
    offers.lowPrice = minPrice;
    offers.highPrice = maxPrice;
  } else if (maxPrice != null) {
    offers.price = maxPrice;
  }

  const additionalProperty: any[] = [];
  if (issue.classification.exchange_platform) {
    additionalProperty.push({ "@type": "PropertyValue", "name": "Listed Exchange", "value": issue.classification.exchange_platform });
  }
  if (lotSize != null) {
    additionalProperty.push({ "@type": "PropertyValue", "name": "Lot Size (Shares)", "value": String(lotSize) });
  }
  if (lotSize != null && maxPrice != null) {
    additionalProperty.push({ "@type": "PropertyValue", "name": "Minimum Investment (INR)", "value": String(lotSize * maxPrice) });
  }
  if (issue.issue_size.text) {
    additionalProperty.push({ "@type": "PropertyValue", "name": "Issue Size (₹ Crore)", "value": String(issue.issue_size.text).replace(/^₹\s*/, '') });
  }
  if (issue.parties.lead_managers.length > 0) {
    additionalProperty.push({ "@type": "PropertyValue", "name": "Book Running Lead Manager", "value": issue.parties.lead_managers.join(', ') });
  }
  if (issue.parties.registrar) {
    additionalProperty.push({ "@type": "PropertyValue", "name": "Registrar", "value": issue.parties.registrar });
  }
  const subscribed = issue.subscription.trajectory.length
    ? (issue.subscription.trajectory[issue.subscription.trajectory.length - 1].total?.times ?? issue.subscription.times)
    : issue.subscription.times;
  if (subscribed != null) {
    additionalProperty.push({ "@type": "PropertyValue", "name": "Overall Subscription (times)", "value": subscribed.toFixed(2) });
  }

  schemas.push({
    "@context": "https://schema.org",
    "@type": "FinancialProduct",
    "name": `${company} ${issueType}`,
    "description": `Detailed Indian primary market data for the ${company} ${issueType} on ${issue.classification.exchange_platform || 'NSE and BSE'}. Covers price band, lot size, subscription status, bidding timeline, allotment, and listing performance.`,
    "provider": {
      "@type": "Organization",
      "name": "IPO Watch",
      "url": `${SITE_URL}/`
    },
    "areaServed": { "@type": "Country", "name": "India" },
    "offers": offers,
    ...(additionalProperty.length > 0 ? { "additionalProperty": additionalProperty } : {})
  });

  // 3. Subscription Event
  if (issue.timeline.open_date && issue.timeline.close_date) {
    schemas.push({
      "@context": "https://schema.org",
      "@type": "Event",
      "name": `${company} ${issueType} Subscription Window`,
      "description": `Public subscription period for the ${company} ${issueType}. Investors can place bids through ASBA or UPI via registered brokers and trading members.`,
      "startDate": `${issue.timeline.open_date}T10:00:00+05:30`,
      "endDate": `${issue.timeline.close_date}T17:00:00+05:30`,
      "eventStatus": "https://schema.org/EventScheduled",
      "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
      "location": {
        "@type": "Place",
        "name": issue.classification.exchange_platform || "NSE and BSE",
        "address": { "@type": "PostalAddress", "addressCountry": "IN" }
      },
      "organizer": { "@type": "Organization", "name": "IPO Watch", "url": `${SITE_URL}/` }
    });
  }

  // 4. Listing Event (if listing date is in the future or recent past)
  if (issue.timeline.listing_date && !issue.timeline.open_date) {
    schemas.push({
      "@context": "https://schema.org",
      "@type": "Event",
      "name": `${company} ${issueType} Stock Market Listing`,
      "description": `${company} shares begin trading on ${issue.classification.exchange_platform || 'NSE and BSE'}.`,
      "startDate": `${issue.timeline.listing_date}T09:15:00+05:30`,
      "eventStatus": "https://schema.org/EventScheduled",
      "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
      "location": {
        "@type": "Place",
        "name": issue.classification.exchange_platform || "NSE and BSE",
        "address": { "@type": "PostalAddress", "addressCountry": "IN" }
      }
    });
  }

  return schemas;
}
