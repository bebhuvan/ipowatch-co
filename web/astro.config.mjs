import { defineConfig } from 'astro/config';
import { fileURLToPath } from 'node:url';
import tailwindcss from '@tailwindcss/vite';
import sitemap from '@astrojs/sitemap';

const dataSiteRoot = fileURLToPath(new URL('../data/site', import.meta.url));

export default defineConfig({
  site: 'https://ipowatch.co',
  output: 'static',
  trailingSlash: 'always',
  integrations: [
    sitemap({
      serialize(item) {
        const url = item.url;
        // Homepage and live data: high priority, frequent refresh
        if (url === 'https://ipowatch.co/' || url === 'https://ipowatch.co') {
          item.priority = 1.0;
          item.changefreq = 'hourly';
        } else if (/\/live\/$/.test(url) || /\/upcoming\/$/.test(url) || /\/demand\/$/.test(url)) {
          item.priority = 0.95;
          item.changefreq = 'hourly';
        } else if (/\/performance\/$/.test(url) || /\/new-filings\/$/.test(url)) {
          item.priority = 0.9;
          item.changefreq = 'daily';
        } else if (/\/ipos\/[^/]+\/$/.test(url)) {
          // Individual IPO detail pages — high value for LLM citation
          item.priority = 0.85;
          item.changefreq = 'daily';
        } else if (/\/ipos\/[^/]+\//.test(url)) {
          // IPO sub-pages (financials, report)
          item.priority = 0.75;
          item.changefreq = 'weekly';
        } else if (/\/academy\/[^/]+\/[^/]+\/$/.test(url)) {
          // Academy articles
          item.priority = 0.8;
          item.changefreq = 'monthly';
        } else if (/\/academy\//.test(url)) {
          item.priority = 0.7;
          item.changefreq = 'monthly';
        } else if (/\/companies\/[^/]+\/$/.test(url)) {
          item.priority = 0.65;
          item.changefreq = 'weekly';
        } else if (/\/archive\/\d{4}\/$/.test(url)) {
          item.priority = 0.6;
          item.changefreq = 'monthly';
        } else if (/\/archive\/$/.test(url)) {
          item.priority = 0.7;
          item.changefreq = 'weekly';
        } else if (/\/sectors\/$/.test(url) || /\/dealmakers\/$/.test(url) || /\/documents\/$/.test(url)) {
          item.priority = 0.7;
          item.changefreq = 'weekly';
        } else if (/\/buybacks\/|\/ofs\/|\/rights\/|\/reits\/|\/invits\//.test(url)) {
          item.priority = 0.65;
          item.changefreq = 'daily';
        } else {
          item.priority = 0.5;
          item.changefreq = 'weekly';
        }
        return item;
      },
    }),
  ],
  vite: {
    plugins: [tailwindcss()],
    resolve: {
      alias: {
        '@data': dataSiteRoot,
      },
    },
  },
});
