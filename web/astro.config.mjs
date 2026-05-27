import { defineConfig } from 'astro/config';
import { fileURLToPath } from 'node:url';
import tailwindcss from '@tailwindcss/vite';

const dataSiteRoot = fileURLToPath(new URL('../data/site', import.meta.url));

export default defineConfig({
  site: 'https://ipowatch.co',
  output: 'static',
  trailingSlash: 'always',
  vite: {
    plugins: [tailwindcss()],
    resolve: {
      alias: {
        '@data': dataSiteRoot,
      },
    },
  },
});
