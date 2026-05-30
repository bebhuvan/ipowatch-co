import type { IssueDetail } from './issues';

export function getWebsiteSchema() {
  return {
    "@context": "https://schema.org",
    "@type": "WebSite",
    "name": "IPO Watch",
    "url": "https://ipowatch.co/",
    "potentialAction": {
      "@type": "SearchAction",
      "target": "https://ipowatch.co/search/?q={search_term_string}",
      "query-input": "required name=search_term_string"
    }
  };
}

export function getOrganizationSchema() {
  return {
    "@context": "https://schema.org",
    "@type": "Organization",
    "name": "IPO Watch",
    "url": "https://ipowatch.co/",
    "logo": "https://ipowatch.co/logo.svg",
    "sameAs": [
      "https://twitter.com/ipowatch_co"
    ],
    "contactPoint": {
      "@type": "ContactPoint",
      "contactType": "customer service",
      "areaServed": "IN",
      "availableLanguage": "English"
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
      "item": item.url.startsWith('http') ? item.url : `https://ipowatch.co${item.url}`
    }))
  };
}

export function getIPOSchemas(issue: IssueDetail) {
  const schemas: any[] = [];
  
  // 1. Breadcrumbs
  const breadcrumbItems = [
    { name: "Home", url: "/" },
    { name: "IPOs", url: "/ipos/" },
    { name: issue.company.name, url: `/ipos/${issue.slug}/` }
  ];
  schemas.push(getBreadcrumbSchema(breadcrumbItems));

  // 2. Financial Product
  const minPrice = issue.pricing.price_band.min;
  const maxPrice = issue.pricing.price_band.max;
  const issuePrice = issue.pricing.issue_price;
  
  const offers: any = {
    "@type": "Offer",
    "priceCurrency": "INR",
  };
  
  if (issuePrice != null) {
    offers.price = issuePrice;
  } else if (minPrice != null && maxPrice != null) {
    offers.lowPrice = minPrice;
    offers.highPrice = maxPrice;
  } else if (maxPrice != null) {
    offers.price = maxPrice;
  }

  const financialProduct = {
    "@context": "https://schema.org",
    "@type": "FinancialProduct",
    "name": `${issue.company.name} ${issue.classification.issue_type || 'IPO'}`,
    "description": `Detailed Indian primary market information for ${issue.company.name} ${issue.classification.issue_type || 'IPO'}, including bidding timelines, price band, lot size, subscription details, and post-listing returns on BSE & NSE.`,
    "provider": {
      "@type": "Organization",
      "name": issue.classification.exchange_platform || "NSE and BSE Exchanges",
      "url": "https://ipowatch.co"
    },
    "offers": offers
  };
  schemas.push(financialProduct);

  // 3. Event (Subscription Window)
  if (issue.timeline.open_date && issue.timeline.close_date) {
    const event = {
      "@context": "https://schema.org",
      "@type": "Event",
      "name": `${issue.company.name} IPO Subscription Window`,
      "description": `Bidding period and subscription window for the ${issue.company.name} ${issue.classification.issue_type || 'IPO'}. Investors can place bids through their ASBA or UPI brokerage accounts.`,
      "startDate": `${issue.timeline.open_date}T10:00:00+05:30`,
      "endDate": `${issue.timeline.close_date}T17:00:00+05:30`,
      "eventStatus": "https://schema.org/EventScheduled",
      "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
      "location": {
        "@type": "Place",
        "name": issue.classification.exchange_platform || "NSE and BSE Exchanges",
        "address": {
          "@type": "PostalAddress",
          "addressCountry": "IN"
        }
      },
      "organizer": {
        "@type": "Organization",
        "name": "IPO Watch",
        "url": "https://ipowatch.co"
      }
    };
    schemas.push(event);
  }

  return schemas;
}
