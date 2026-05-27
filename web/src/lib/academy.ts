// Academy section metadata. Editorial labels, ordering, and one-line
// blurbs for the section index pages. Keeping this here (rather than
// in frontmatter) so a section can exist before it has any articles.

export type AcademySection =
  | 'foundations'
  | 'mechanics'
  | 'regulation'
  | 'glossary'
  | 'timeline';

export type SectionMeta = {
  slug: AcademySection;
  ordinal: string;
  label: string;
  blurb: string;
};

export const SECTION_ORDER: SectionMeta[] = [
  {
    slug: 'foundations',
    ordinal: 'I',
    label: 'Foundations',
    blurb:
      'What an IPO is, why companies list, how primary issue types differ — IPO, FPO, OFS, QIP, rights issue, buyback.',
  },
  {
    slug: 'mechanics',
    ordinal: 'II',
    label: 'Mechanics',
    blurb:
      'Book-building, price band, anchor book, retail / NII / QIB allotment, listing day, lock-in.',
  },
  {
    slug: 'regulation',
    ordinal: 'III',
    label: 'Regulation',
    blurb:
      'SEBI ICDR, the DRHP and RHP, ASBA and UPI in public issues, insider trading, disclosure obligations.',
  },
  {
    slug: 'glossary',
    ordinal: 'IV',
    label: 'Glossary',
    blurb:
      'Plain-English definitions of every term used elsewhere on the site.',
  },
  {
    slug: 'timeline',
    ordinal: 'V',
    label: 'Timeline',
    blurb:
      'Indian primary-market regulation from 1947 to today, the key amendments, the issues that forced them.',
  },
];

export function getSection(slug: AcademySection): SectionMeta {
  const m = SECTION_ORDER.find((s) => s.slug === slug);
  if (!m) throw new Error(`Unknown Academy section: ${slug}`);
  return m;
}
