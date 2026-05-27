// Academy glossary. Plain-English definitions of every term used across
// the Academy and the data site. Each definition is grounded in the same
// primary sources (SEBI ICDR / Buyback / PIT Regulations and the ICDR
// Master Circular) the articles cite. `see` links to the article that
// explains the term in depth, where one exists.

export type GlossaryTerm = {
  term: string;
  short: string; // optional acronym expansion or one-line tag
  def: string;
  see?: { label: string; href: string };
};

export const GLOSSARY: GlossaryTerm[] = [
  {
    term: 'Anchor investor',
    short: 'a pre-IPO institutional buyer',
    def: 'A qualified institutional buyer that commits at least ₹10 crore to a main-board IPO and bids one day before the issue opens. Up to 60% of the QIB portion can be set aside for anchors; half of an anchor’s shares are locked in for 90 days and half for 30.',
    see: { label: 'How allotment works', href: '/academy/mechanics/how-allotment-works/' },
  },
  {
    term: 'ASBA',
    short: 'Application Supported by Blocked Amount',
    def: 'The mandatory way to apply for an Indian public issue: instead of paying the company, you authorise your bank to block the application money in your own account. It is debited only if you are allotted shares; otherwise the block is released.',
    see: { label: 'ASBA and UPI', href: '/academy/regulation/how-asba-upi-works/' },
  },
  {
    term: 'Book-building',
    short: 'price discovery from demand',
    def: 'The process of pricing an issue by collecting bids over a few days within a published price band, rather than fixing one price upfront. The final price is set where the book is comfortably covered.',
    see: { label: 'Book-building', href: '/academy/mechanics/how-book-building-works/' },
  },
  {
    term: 'Buyback',
    short: 'a company buying its own shares',
    def: 'A company using its own cash to buy its shares back from holders and cancel them — the mirror image of an IPO. Capped at 25% of paid-up capital and free reserves, and the shares must be extinguished.',
    see: { label: 'What is a buyback', href: '/academy/foundations/what-is-a-buyback/' },
  },
  {
    term: 'Connected person',
    short: 'an insider by association',
    def: 'Under the insider-trading rules, anyone associated with a company in the prior six months — by office, contract, or frequent contact — in a way that gives, or is expected to give, access to unpublished price-sensitive information.',
    see: { label: 'Insider trading', href: '/academy/regulation/insider-trading-explained/' },
  },
  {
    term: 'Cut-off price',
    short: 'retail bidding shorthand',
    def: 'An option for retail bidders to accept whatever final price emerges from book-building, rather than naming a price within the band. The applicant agrees to pay the discovered price up to the cap.',
  },
  {
    term: 'DRHP / RHP',
    short: 'draft and final prospectus',
    def: 'The Draft Red Herring Prospectus is the offer document filed with SEBI for review; the Red Herring Prospectus is the near-final version filed before the issue opens. In a book-built IPO the DRHP deliberately omits the price.',
  },
  {
    term: 'Face value',
    short: 'a share’s nominal value',
    def: 'The nominal value a company assigns each share when it is created. An IPO’s floor price and final price can never be below face value.',
    see: { label: 'Book-building', href: '/academy/mechanics/how-book-building-works/' },
  },
  {
    term: 'FPO',
    short: 'Further Public Offer',
    def: 'A public issue of shares by a company that is already listed. It can be a fresh issue, an offer for sale, or both — the same public mechanics as an IPO, but the company already trades.',
    see: { label: 'The one map', href: '/academy/foundations/the-primary-issue-family/' },
  },
  {
    term: 'Fresh issue',
    short: 'new shares, new money',
    def: 'The part of an offering where the company creates new shares and keeps the proceeds. Contrast with an offer for sale, where existing shares change hands and the company gets nothing.',
    see: { label: 'What is an IPO', href: '/academy/foundations/what-is-an-ipo/' },
  },
  {
    term: 'Fugitive economic offender',
    short: 'a disqualifying status',
    def: 'A person declared a fugitive economic offender under the Fugitive Economic Offenders Act, 2018. A company cannot make an IPO if any promoter or director is one.',
    see: { label: 'What is an IPO', href: '/academy/foundations/what-is-an-ipo/' },
  },
  {
    term: 'Generally available information',
    short: 'public information',
    def: 'Information accessible to the public on a non-discriminatory basis. Once price-sensitive information becomes generally available — filed with the exchanges or broadly published — it is no longer UPSI.',
    see: { label: 'Insider trading', href: '/academy/regulation/insider-trading-explained/' },
  },
  {
    term: 'Insider',
    short: 'who the trading ban covers',
    def: 'Either a connected person, or anyone in possession of (or with access to) unpublished price-sensitive information — whether or not they work for the company.',
    see: { label: 'Insider trading', href: '/academy/regulation/insider-trading-explained/' },
  },
  {
    term: 'IPO',
    short: 'Initial Public Offer',
    def: 'The first time a company sells its shares to the public, after which it is listed and its shares trade on the exchanges. The only public-issue route open to an unlisted company.',
    see: { label: 'What is an IPO', href: '/academy/foundations/what-is-an-ipo/' },
  },
  {
    term: 'NII / HNI',
    short: 'Non-Institutional Investor',
    def: 'Applicants who bid above ₹2 lakh and are not institutions. The category is split into small HNIs (sHNI, ₹2–10 lakh — one-third of the NII portion) and big HNIs (bHNI, above ₹10 lakh — two-thirds).',
    see: { label: 'How allotment works', href: '/academy/mechanics/how-allotment-works/' },
  },
  {
    term: 'OFS',
    short: 'Offer For Sale',
    def: 'A sale of existing shares by current shareholders. No new shares are created and the company raises nothing; the proceeds go to the selling shareholders.',
    see: { label: 'The one map', href: '/academy/foundations/the-primary-issue-family/' },
  },
  {
    term: 'Price band',
    short: 'floor and cap',
    def: 'The range an issuer publishes for a book-built issue. The cap can be at most 120% of the floor and at least 105% of it, and the band must be advertised at least two working days before the issue opens.',
    see: { label: 'Book-building', href: '/academy/mechanics/how-book-building-works/' },
  },
  {
    term: 'QIB',
    short: 'Qualified Institutional Buyer',
    def: 'The big institutional investors — mutual funds, insurers, pension funds, foreign portfolio investors, banks — that SEBI treats as sophisticated. Up to 50% of a normal IPO’s net offer goes to QIBs; at least 75% in a compulsory-QIB issue.',
    see: { label: 'How allotment works', href: '/academy/mechanics/how-allotment-works/' },
  },
  {
    term: 'QIP',
    short: 'Qualified Institutions Placement',
    def: 'A listed company placing shares privately with QIBs, on the strength of a special shareholder resolution. It skips the public-offer machinery, which makes it the quick route — but only institutions can buy.',
    see: { label: 'The one map', href: '/academy/foundations/the-primary-issue-family/' },
  },
  {
    term: 'Registrar (RTA)',
    short: 'Registrar and Transfer Agent',
    def: 'The firm appointed by the issuer to process applications, run PAN verification, finalise the basis of allotment, and instruct debits, unblocks, and demat credits.',
    see: { label: 'The T+3 timeline', href: '/academy/mechanics/ipo-listing-timeline/' },
  },
  {
    term: 'Retail individual investor',
    short: 'RII',
    def: 'An individual who applies for not more than ₹2 lakh in an issue. At least 35% of a normal IPO’s net offer is reserved for retail; in an oversubscribed issue, allotment is by lottery.',
    see: { label: 'How allotment works', href: '/academy/mechanics/how-allotment-works/' },
  },
  {
    term: 'Rights issue',
    short: 'shares offered to existing holders',
    def: 'An offer of new shares to a listed company’s existing shareholders as on a record date, in proportion to their holding — not to the general public. Holders can subscribe, sell the rights, or let them lapse.',
    see: { label: 'The one map', href: '/academy/foundations/the-primary-issue-family/' },
  },
  {
    term: 'SCSB',
    short: 'Self-Certified Syndicate Bank',
    def: 'A SEBI-registered bank that offers ASBA — it blocks the application money in the applicant’s account and later debits it on allotment or releases it on non-allotment.',
    see: { label: 'ASBA and UPI', href: '/academy/regulation/how-asba-upi-works/' },
  },
  {
    term: 'Selling shareholder',
    short: 'who sells in an OFS',
    def: 'Any shareholder offering existing shares for sale in a public issue. The proceeds of their shares go to them, not to the company.',
    see: { label: 'The one map', href: '/academy/foundations/the-primary-issue-family/' },
  },
  {
    term: 'Specified securities',
    short: 'equity and convertibles',
    def: 'Equity shares and convertible securities — the instruments an IPO or other public issue is made in.',
  },
  {
    term: 'Sponsor bank',
    short: 'the UPI conduit',
    def: 'A banker to the issue, appointed by the company, that acts as the conduit between the stock exchanges and NPCI — pushing the UPI mandate requests of retail applicants into the payment system.',
    see: { label: 'ASBA and UPI', href: '/academy/regulation/how-asba-upi-works/' },
  },
  {
    term: 'Tender offer',
    short: 'a buyback method',
    def: 'A buyback in which the company offers, through a letter of offer, to buy shares back from existing holders on a proportionate basis at a fixed price.',
    see: { label: 'What is a buyback', href: '/academy/foundations/what-is-a-buyback/' },
  },
  {
    term: 'UPSI',
    short: 'Unpublished Price-Sensitive Information',
    def: 'Information about a company or its securities that is not generally available and, once public, is likely to materially move the price — results, dividends, capital-structure changes, M&A, key-management or rating changes.',
    see: { label: 'Insider trading', href: '/academy/regulation/insider-trading-explained/' },
  },
  {
    term: 'Wilful defaulter',
    short: 'a disqualifying status',
    def: 'A borrower categorised by the RBI as having defaulted despite the ability to pay (or as a fraudulent borrower). A company with such a person among its promoters or directors cannot make an IPO.',
    see: { label: 'What is an IPO', href: '/academy/foundations/what-is-an-ipo/' },
  },
];
