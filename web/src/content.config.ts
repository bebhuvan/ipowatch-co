import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

// YAML parses unquoted ISO dates into Date objects. Coerce back to
// ISO YYYY-MM-DD strings so downstream rendering can treat the field
// as a plain string regardless of how the markdown author wrote it.
const isoDate = z
  .union([z.string(), z.date()])
  .transform((v) => (v instanceof Date ? v.toISOString().slice(0, 10) : v));

const academy = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/academy' }),
  schema: z.object({
    slug: z.string(),
    section: z.enum(['foundations', 'mechanics', 'regulation', 'glossary', 'timeline']),
    kicker: z.string(),
    title: z.string(),
    dek: z.string(),
    reading_time_minutes: z.number(),
    published_at: isoDate,
    updated_at: isoDate.optional(),
    status: z.enum(['draft', 'review', 'published']),
    sources: z.array(z.object({
      source_id: z.string(),
      title: z.string(),
      url: z.string().url(),
      publisher: z.string(),
      published_at: isoDate.nullable(),
      source_tier: z.enum(['primary', 'secondary', 'enrichment']),
      document_kind: z.string(),
    })),
    pull_quotes: z.array(z.object({
      after_paragraph_starts_with: z.string(),
      quote: z.string(),
    })).default([]),
    sidebars: z.array(z.object({
      after_section_slug: z.string(),
      title: z.string(),
      body_markdown: z.string(),
    })).default([]),
  }),
});

export const collections = { academy };
