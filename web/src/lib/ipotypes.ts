// TypeScript types for the public V3 dataset (data/ipo_watch_v3). Hand-written to match
// docs/schema/v2/*.schema.json and the records actually emitted by the pipeline.
// Units: *_paise = integer paise (₹×100); *_bps = integer basis points (1%=100);
// *_x = decimal STRING (e.g. "7.5700"). Almost everything is nullable.

export type QualityState = 'clean' | 'review' | 'quarantine';
export type IssueType =
  | 'IPO' | 'FPO' | 'Rights' | 'Buyback' | 'OFS' | 'NCD'
  | 'SGB' | 'InvIT' | 'REIT' | 'Call Money' | 'Others';
export type IssueStatus =
  | 'Filed' | 'Open' | 'Closed' | 'Listed' | 'Withdrawn' | 'Upcoming';
export type BoardType = 'Main Board' | 'SME Board' | null;
export type Confidence = 'high' | 'medium' | 'low';

/** The metadata envelope every record opens with. */
export interface Envelope {
  $schema: string;
  schema_version: string;
  schema_url_self: string | null;
  dataset: string;
  dataset_version: string;
  generated_at: string;
  generated_by: string;
  time_zone: string;
  currency: string;
  language: string;
  sources: Array<Record<string, unknown>>;
  field_provenance: Record<string, { source?: string; snapshot_at?: string; rule_id?: string }>;
  data_quality: { state: QualityState; errors: unknown[]; warnings: unknown[] };
  freshness: Record<string, string>;
  license: string;
  notes: string | null;
}

/** Compact summary used in issues/index.json and company.issues[]. */
export interface IssueSummary {
  slug: string;
  public_slug?: string | null;
  url_path: string;
  company_name: string;
  symbol: string | null;
  isin: string | null;
  issue_type: IssueType | null;
  board_type: BoardType;
  status: IssueStatus | null;
  data_quality_state: QualityState | null;
  source_count: number | null;
  open_date: string | null;
  close_date: string | null;
  listing_date: string | null;
  price_band_lower_paise: number | null;
  price_band_upper_paise: number | null;
  issue_price_paise: number | null;
  issue_size_paise: number | null;
  overall_times_x: string | null;
  listing_gain_bps: number | null;
  current_gain_bps: number | null;
}

export interface Issue extends Envelope {
  slug: string;
  public_slug?: string | null;
  identity: {
    company_name: string;
    slug: string;
    symbol: string | null;
    isin: string | null;
    issue_type: IssueType | null;
    board_type: BoardType;
    status: IssueStatus | null;
    aliases?: string[];
  };
  pricing?: {
    price_band_lower_paise?: number | null;
    price_band_upper_paise?: number | null;
    issue_price_paise?: number | null;
    issue_size_paise?: number | null;
    issue_size_shares?: number | null;
    face_value_paise?: number | null;
    lot_size_shares?: number | null;
    market_lot?: number | null;
    tick_size_paise?: number | null;
  };
  timeline?: {
    open_date?: string | null;
    close_date?: string | null;
    listing_date?: string | null;
    allotment_date?: string | null;
  };
  subscription?: {
    overall_times_x?: string | null;
    qib_times_x?: string | null;
    retail_times_x?: string | null;
    hni_times_x?: string | null;
    anchor?: Record<string, unknown> | null;
    categories?: Array<{ category: string; times_x?: string | null }>;
    consolidated?: Record<string, unknown> | null;
    by_exchange?: Record<string, unknown>;
  };
  listing_performance?: {
    listing_open_price_paise?: number | null;
    listing_close_price_paise?: number | null;
    current_price_paise?: number | null;
    listing_gain_bps?: number | null;
    current_gain_bps?: number | null;
  };
  documents?: {
    drhp_url?: string | null;
    rhp_url?: string | null;
    prospectus_url?: string | null;
    basis_allotment_url?: string | null;
  };
  parties?: {
    lead_managers?: string[] | string | null;
    registrar?: string | null;
    sponsor_bank?: string | null;
  };
  exchange_details?: Record<string, unknown>;
}

export interface Company extends Envelope {
  slug: string;
  public_slug?: string | null;
  url_path: string;
  company_name: string;
  normalized_name: string;
  issue_count: number;
  issues: IssueSummary[];
}

/** A single provenance leaf in a prospectus extraction. */
export interface ProvLeaf<T = string> {
  value: T | null;
  raw_excerpt?: string | null;
  source_page?: number | null;
  source_section?: string | null;
  confidence?: Confidence;
}

export interface Prospectus extends Envelope {
  hero?: Record<string, ProvLeaf | unknown>;
  company_about?: Record<string, unknown>;
  the_offer?: Record<string, unknown>;
  industry_landscape?: Record<string, unknown>;
  macros?: Record<string, unknown>;
  business_model?: Record<string, unknown>;
  financial_highlights?: Record<string, unknown>;
  valuation?: Record<string, unknown>;
  risks?: {
    total_disclosed_count?: ProvLeaf<number>;
    by_category?: Array<{
      category: string;
      count: number;
      top_risks?: Array<{
        title?: ProvLeaf;
        summary?: ProvLeaf;
        severity_inferred?: ProvLeaf;
        provenance?: ProvLeaf;
      }>;
    }>;
    qualitative_themes?: unknown;
  };
  promoter_and_shareholding_and_litigation?: Record<string, unknown>;
  meta?: Record<string, unknown>;
}

export interface BidBucket {
  shares_bid: number | null;
  shares_offered: number | null;
  times: number | null; // raw multiple (NOT the _x decimal-string convention)
}

export interface TrajectoryObservation {
  observed_at: string;
  source: string | null;
  source_updated_at: string | null;
  total: BidBucket;
  categories: Record<string, BidBucket>;
}

export interface DemandCurve {
  observed_at: string;
  source: string | null;
  scope: 'nse' | 'all' | string;
  source_updated_at: string | null;
  points: Array<{
    price_paise: number;
    cumulative_quantity: number;
  }>;
}

export interface Trajectory extends Envelope {
  issue_slug: string;
  frozen_at: string | null;
  observation_count: number;
  observations: TrajectoryObservation[];
  demand_curve_count?: number;
  demand_curves?: DemandCurve[];
}
