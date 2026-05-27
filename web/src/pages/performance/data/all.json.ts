import type { APIRoute } from 'astro';
import { performanceIndex } from '../../../lib/performance';

export const GET: APIRoute = () => new Response(JSON.stringify(performanceIndex(), null, 2), {
  headers: {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'public, max-age=300',
  },
});
