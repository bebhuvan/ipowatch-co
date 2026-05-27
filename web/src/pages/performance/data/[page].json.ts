import type { APIRoute } from 'astro';
import { performancePage, performanceSummary } from '../../../lib/performance';

export function getStaticPaths() {
  const summary = performanceSummary();
  return Array.from({ length: summary.page_count }, (_, idx) => ({
    params: { page: `page-${idx + 1}` },
  }));
}

export const GET: APIRoute = ({ params }) => {
  const page = Number(String(params.page ?? 'page-1').replace(/^page-/, '')) || 1;
  return new Response(JSON.stringify(performancePage(page), null, 2), {
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'public, max-age=300',
    },
  });
};
