// Response and request contracts of the AgniSetu API (version 1). These mirror backend/agnisetu.

export type Metric = "count" | "frp";
export type ConfidenceFilter = "all" | "nominal" | "high";
export type PlatformKey = "TERRA" | "AQUA" | "SNPP" | "NOAA20" | "NOAA21";
export type SeriesKind = "harmonized" | "modis" | "snpp" | "noaa20";
export type Resolution = "weekly" | "monthly" | "daily";
export type ValueType = "missing" | "reference" | "viirs_calibrated" | "modis_harmonized";
export type AnomalyClass = "far_below" | "below" | "near_normal" | "above" | "far_above";
export type AlertLevel = "minimal" | "below_normal" | "normal" | "elevated" | "high" | "extreme";

export type Position = [number, number];
export interface PolygonGeometry {
  type: "Polygon";
  coordinates: Position[][];
}
export interface MultiPolygonGeometry {
  type: "MultiPolygon";
  coordinates: Position[][][];
}
export type AreaGeometry = PolygonGeometry | MultiPolygonGeometry;

export type AoiRef = { preset_id: string } | { geometry: AreaGeometry; name?: string };

export interface Options {
  metric: Metric;
  confidence: ConfidenceFilter;
}

export interface AoiInfo {
  id: string;
  name: string;
  source: "preset" | "custom";
  preset_id: string | null;
  area_km2: number;
  bbox: [number, number, number, number];
  vertices: number;
  is_rectangle: boolean;
  repaired: boolean;
  domain_fraction: number;
  domain_countries: string[];
}

export interface MetricInfo {
  key: Metric;
  label: string;
  unit: string;
  unit_short: string;
}

export interface Envelope {
  aoi: AoiInfo;
  options: Options;
  metric: MetricInfo;
  confidence: { key: ConfidenceFilter; label: string };
  record: { start: string; end: string; last_complete_day: string };
  store_revision: number;
}

export interface PlatformCoverage {
  id: number;
  key: PlatformKey;
  label: string;
  instrument: "MODIS" | "VIIRS";
  resolution_m: number;
  overpass_local: string;
  record_start: string;
  first_observed: string | null;
  last_observed: string | null;
  last_any: string | null;
  days_observed: number;
  days_outage: number;
  days_provisional: number;
  detections: number;
  archive_through: string | null;
}

export interface CoverageSummary {
  ready: boolean;
  revision: number;
  countries: string[];
  record_start?: string;
  last_day?: string;
  last_complete_day?: string;
  platforms: PlatformCoverage[];
  static_mask?: { cells: number; cell_deg: number; built_at: string } | null;
  updated_at?: string;
}

export interface Job {
  id: string;
  kind: "refresh_nrt" | "backfill_api";
  trigger: string;
  state: "running" | "succeeded" | "failed";
  started_at: string;
  finished_at: string | null;
  result: unknown;
  error: string | null;
}

export interface Meta {
  product: string;
  tagline: string;
  version: string;
  coverage: CoverageSummary;
  metrics: MetricInfo[];
  confidence_filters: { key: ConfidenceFilter; label: string }[];
  platforms: {
    id: number;
    key: PlatformKey;
    label: string;
    instrument: string;
    resolution_m: number;
    overpass_local: string;
    record_start: string;
  }[];
  limits: {
    max_body_bytes: number;
    max_aoi_vertices: number;
    min_aoi_area_km2: number;
    max_aoi_area_km2: number;
    max_map_points: number;
    max_map_window_days: number;
  };
  default_preset: string;
  has_map_key: boolean;
  admin_enabled: boolean;
  nrt_refresh_minutes: number;
  last_refresh: Job | null;
  boundary_sources: { country: string; level: string; source: string; license: string; year: string }[];
  server_time: string;
}

export interface PresetEntry {
  id: string;
  name: string;
  level: "region" | "country" | "state" | "district";
  country: string | null;
  parent: string | null;
  bbox: [number, number, number, number];
  area_km2: number;
  description: string | null;
}

export interface PresetDetail extends PresetEntry {
  aoi: AoiInfo;
  geometry: AreaGeometry;
}

export interface ReferenceStates {
  type: "FeatureCollection";
  features: {
    type: "Feature";
    properties: { id: string; name: string; country: string; label_lon: number; label_lat: number; area_km2: number };
    geometry: AreaGeometry;
  }[];
}

export interface ValidatedAoi {
  aoi: AoiInfo;
  geometry: AreaGeometry;
}

export interface SummaryResponse extends Envelope {
  platforms: {
    key: PlatformKey;
    label: string;
    instrument: string;
    detections: number;
    frp_mw: number;
    static_removed: number;
    first_detection: string | null;
    last_detection: string | null;
  }[];
  detections_total: number;
  detections_all_confidence: number;
  static_removed_total: number;
  static_share: number;
  harmonized_annual: {
    mean: number | null;
    median: number | null;
    peak_year: number | null;
    peak_value: number | null;
    lowest_year: number | null;
    lowest_value: number | null;
    complete_years: number;
  };
  days_by_type: Record<ValueType, number>;
  latest: { date: string; last_7_days: number | null; last_30_days: number | null };
  timing: { cube_seconds: number; analysis_seconds: number };
}

export interface ClimatologyData {
  years_used: number[];
  mean: (number | null)[];
  p10: (number | null)[];
  p25: (number | null)[];
  median: (number | null)[];
  p75: (number | null)[];
  p90: (number | null)[];
  min: (number | null)[];
  max: (number | null)[];
  n_years: number[];
}

export interface CriticalPeriod {
  id: string;
  name: string;
  start_week: number;
  end_week: number;
  weeks: number[];
  peak_week: number;
  wraps_year: boolean;
  window_label: string;
  peak_week_label: string;
  share_of_annual: number;
  median_total: number | null;
  onset: { median_offset: number | null; median: string | null; p25: string | null; p75: string | null; earliest: string | null };
  peak: { median_offset: number | null; median: string | null };
  end: { median_offset: number | null; median: string | null; p25: string | null; p75: string | null };
  median_duration_days: number | null;
  years_with_timing: number;
}

export interface CalendarResponse extends Envelope {
  series: SeriesKind;
  resolution: Resolution;
  years: number[];
  value_types: Record<string, ValueType>;
  matrix: (number | null)[][];
  lower?: (number | null)[][];
  upper?: (number | null)[][];
  vtype?: number[][];
  days_covered?: number[][];
  scale_max: number | null;
  annual: { total: (number | null)[]; days_fraction: (number | null)[] };
  weeks: { index: number; label: string; start_doy: number }[];
  baseline: { start: number; end: number; years_used: number[] };
  climatology: ClimatologyData;
  critical_periods: CriticalPeriod[];
  era_markers: { date: string; label: string }[];
  // Present when resolution is "monthly".
  months?: { index: number; label: string; short: string }[];
  monthly_climatology?: {
    p10: (number | null)[];
    p25: (number | null)[];
    median: (number | null)[];
    p75: (number | null)[];
    p90: (number | null)[];
    mean: (number | null)[];
    max: (number | null)[];
    n_years: number[];
  };
  top_months?: { year: number; month: number; label: string; value: number | null; ratio_to_median: number | null }[];
  top_years?: { year: number; value: number | null; ratio_to_median: number | null }[];
}

export interface ModelCandidate {
  name: string;
  label: string;
  selected: boolean;
  params: Record<string, number>;
  wape: number | null;
  bias: number | null;
  r2_log: number | null;
  annual_mape: number | null;
}

export interface PredictorSet {
  key: "J1" | "TA" | "A" | "T";
  label: string;
  platforms: PlatformKey[];
  available: boolean;
  reason?: string;
  scope?: string;
  scope_label?: string;
  n_weeks?: number;
  n_years?: number;
  overlap_start?: string | null;
  overlap_end?: string | null;
  predictor_detections?: number;
  selected?: string;
  selected_label?: string;
  candidates?: ModelCandidate[];
  interval?: { coverage: number; log_low: number[]; log_high: number[]; thresholds: number[] };
  factor_curve?: { typical_x: number | null; factor: (number | null)[] };
  days_used?: number;
  scatter?: { x: (number | null)[]; y: (number | null)[]; week: number[]; year: number[] };
  validation?: {
    dates: string[];
    observed: (number | null)[];
    predicted: (number | null)[];
    lower: (number | null)[];
    upper: (number | null)[];
  };
}

export interface CoverageSegment {
  state: "pre_launch" | "not_ingested" | "outage" | "observed" | "provisional";
  start: string;
  end: string;
  days: number;
}

export interface HarmonizationResponse extends Envelope {
  reference: { key: PlatformKey; label: string };
  sets: PredictorSet[];
  annual: {
    years: number[];
    modis_raw: (number | null)[];
    snpp_raw: (number | null)[];
    noaa20_raw: (number | null)[];
    naive: (number | null)[];
    harmonized: (number | null)[];
    harmonized_lower: (number | null)[];
    harmonized_upper: (number | null)[];
  };
  continuity: {
    before_years: number[];
    after_years: number[];
    naive_step: number | null;
    harmonized_step: number | null;
    modis_step: number | null;
  };
  days_by_type: Record<ValueType, number>;
  coverage_timeline: { key: PlatformKey; label: string; instrument: string; segments: CoverageSegment[] }[];
  priority: { key: string; label: string; available: boolean }[];
}

export interface SeasonRow {
  year: number;
  available: boolean;
  total: number | null;
  lower?: number | null;
  upper?: number | null;
  estimated_share?: number | null;
  reference_median?: number | null;
  ratio_to_median?: number | null;
  reference_mean?: number | null;
  /** Fractional departure from the mean of the other baseline years: 0.25 is 25 percent above. */
  anomaly_vs_mean?: number | null;
  percentile?: number | null;
  robust_z?: number | null;
  class?: AnomalyClass | null;
  record_high?: boolean;
  record_low?: boolean;
  onset?: string | null;
  peak?: string | null;
  end?: string | null;
  onset_offset?: number | null;
  peak_offset?: number | null;
  end_offset?: number | null;
  onset_shift_days?: number | null;
  peak_shift_days?: number | null;
  end_shift_days?: number | null;
}

export interface Trend {
  available: boolean;
  n: number;
  first_year?: number;
  last_year?: number;
  slope_per_year?: number | null;
  intercept?: number | null;
  slope_per_decade?: number | null;
  percent_per_decade?: number | null;
  mk_z?: number | null;
  p_value?: number | null;
  significant?: boolean;
  direction?: "increasing" | "decreasing" | "flat";
  text: string;
}

export interface AnomaliesResponse extends Envelope {
  years: number[];
  baseline: { start: number; end: number; years_used: number[] };
  periods: CriticalPeriod[];
  classes: { key: AnomalyClass; label: string }[];
  weekly: { percentile: (number | null)[][]; class_index: number[][] };
  weeks: { index: number; label: string }[];
  selected_period: string | null;
  seasons: SeasonRow[];
  ranking: SeasonRow[];
  trends: { period_id: string; total: Trend; onset: Trend; peak: Trend } | null;
  reference: { p10: number | null; p25: number | null; median: number | null; p75: number | null; p90: number | null; n_years: number } | null;
}

export interface RecentWindow {
  days: number;
  start: string;
  end: string;
  value: number | null;
  reference_years: number;
  available: boolean;
  reason?: string;
  percentile?: number | null;
  ratio_to_median?: number | null;
  reference_p10?: number | null;
  reference_p25?: number | null;
  reference_median?: number | null;
  reference_p75?: number | null;
  reference_p90?: number | null;
  reference_max?: number | null;
  history?: { year: number; value: number | null }[];
}

export interface MonthToDate extends RecentWindow {
  month_name: string;
  label: string;
  days_elapsed: number;
  days_in_month: number;
  full_month_median: number | null;
  full_month_years: number;
  highest: { year: number; value: number | null } | null;
}

export interface SeasonProgress {
  period_id: string;
  period_name: string;
  season_year: number;
  window_start: string;
  window_end: string;
  reference_years: number[];
  available: boolean;
  in_core_window?: boolean;
  reason?: string;
  envelope?: { offsets: number[]; dates: string[]; p10: number[]; p25: number[]; p50: number[]; p75: number[]; p90: number[] };
  current?: { offsets: number[]; dates: string[]; values: number[] };
  to_date?: number | null;
  to_date_median?: number | null;
  to_date_percentile?: number | null;
  share_of_typical_season?: number | null;
  days_elapsed?: number;
  days_total?: number;
}

export type PeriodStatus =
  | {
      status: "in_progress";
      period_id: string;
      period_name: string;
      typical_onset: string;
      typical_end: string;
      day_of_period: number;
      typical_length_days: number;
      days_to_typical_end: number;
    }
  | {
      status: "upcoming";
      period_id: string;
      period_name: string;
      typical_onset: string;
      typical_end: string;
      days_until: number;
      earliest_onset: string | null;
      latest_onset: string | null;
    };

export interface OutlookItem {
  horizon_weeks: number;
  start: string;
  end: string;
  climatology_median: number | null;
  climatology_p10: number | null;
  climatology_p90: number | null;
  available: boolean;
  reason?: string;
  method?: "climatology" | "persistence_scaled_climatology";
  method_label?: string;
  expected?: number | null;
  lower?: number | null;
  upper?: number | null;
  recent_anomaly_ratio?: number | null;
  persistence?: number | null;
  skill_vs_climatology?: number | null;
  hindcast_pairs?: number;
  fallback_reason?: string;
}

export interface WarningResponse extends Envelope {
  as_of: string;
  level: AlertLevel | null;
  level_label: string | null;
  level_rule: string | null;
  level_scale: { key: AlertLevel; label: string }[];
  peak_week_median: number | null;
  last_7_days: RecentWindow;
  last_14_days: RecentWindow;
  month_to_date: MonthToDate;
  recent_sources: Record<ValueType, number>;
  reference_observed_recently: boolean;
  season_progress: SeasonProgress | null;
  critical_period_status: PeriodStatus | null;
  outlook: OutlookItem[];
  bulletin: string[];
  as_of_bounds: { min: string; max: string; default: string };
  is_latest: boolean;
  recent_daily: {
    dates: string[];
    values: (number | null)[];
    vtype: number[];
    climatology_median: (number | null)[];
    climatology_p90: (number | null)[];
  };
  periods: CriticalPeriod[];
  has_map_key: boolean;
}

export interface HotspotBase {
  date_from: string;
  date_to: string;
  bbox: [number, number, number, number];
  platforms: PlatformKey[];
  max_points: number;
  count: number;
  total_in_view: number;
}

export interface HotspotPoints extends HotspotBase {
  mode: "points";
  lon: number[];
  lat: number[];
  platform: number[];
  conf: number[];
  frp: number[];
  day: number[];
  day_offset: number[];
  time: number[];
  scan: number[];
  track: number[];
  ftype: number[];
}

export interface HotspotGrid extends HotspotBase {
  mode: "grid";
  step: number;
  west: number[];
  south: number[];
  n: number[];
  frp: number[];
}

export type HotspotResponse = HotspotPoints | HotspotGrid;

export interface HotspotRequest {
  bbox: [number, number, number, number];
  date_from: string;
  date_to: string;
  platforms?: PlatformKey[];
  confidence: ConfidenceFilter;
  include_static: boolean;
  aoi?: AoiRef;
  clip_to_aoi: boolean;
}

export type RatioGridResponse =
  | { available: false; reason: string }
  | {
      available: true;
      step: number;
      period: { start: string; end: string };
      min_modis: number;
      west: number[];
      south: number[];
      modis: number[];
      viirs: number[];
      ratio: (number | null)[];
      cells: number;
      cells_with_ratio: number;
      overall_ratio: number | null;
      ratio_p10: number | null;
      ratio_median: number | null;
      ratio_p90: number | null;
    };

export interface ArchiveEntry {
  key: string;
  dataset: string;
  country: string;
  year: number;
  rows: number;
  rows_rejected: number;
  bytes: number;
  min_date: string | null;
  max_date: string | null;
  sha256: string;
  url: string;
  ingested_at: string;
}

export interface NrtRow {
  platform: PlatformKey;
  label: string;
  days: number;
  rows: number;
  complete_days: number;
  first_day: string | null;
  last_day: string | null;
  last_complete_day: string | null;
  sources: string[];
  updated_at: string | null;
}

export interface DataCoverageResponse {
  revision: number;
  summary: CoverageSummary;
  archive: ArchiveEntry[];
  archive_totals: { files: number; rows: number; rows_rejected: number; bytes: number; rejected_share: number };
  not_published: string[];
  near_real_time: NrtRow[];
  events: Record<string, string | number>[];
  static_mask: { cells: number; cell_deg: number; built_at: string } | null;
  jobs: Job[];
}

export interface ApiErrorBody {
  error: { code: string; message: string; request_id: string };
}

export interface RangeResponse extends Envelope {
  range: { from: string; to: string; days: number };
  bounds: { min: string; max: string };
  total: number | null;
  lower: number | null;
  upper: number | null;
  days_with_data: number;
  days_missing: number;
  mean_per_day: number | null;
  peak_day: { date: string; value: number | null } | null;
  days_by_type: Record<ValueType, number>;
  platforms: { key: PlatformKey; label: string; days_observed: number; raw_total: number | null }[];
  comparison:
    | { available: false; reason?: string }
    | {
        available: true;
        reference_years: number;
        p10: number | null;
        p25: number | null;
        median: number | null;
        p75: number | null;
        p90: number | null;
        percentile: number | null;
        ratio_to_median: number | null;
        mean: number | null;
        anomaly_vs_mean: number | null;
        rank: number;
        history: { year: number; value: number | null }[];
        year_of_range: number;
      };
  series: {
    resolution: "daily" | "weekly" | "thirty_day";
    labels: string[];
    values: (number | null)[];
    lower: (number | null)[];
    upper: (number | null)[];
    vtype: number[];
    typical: (number | null)[];
  };
}

export interface HotspotExportRequest {
  aoi: AoiRef;
  date_from: string;
  date_to: string;
  platforms?: PlatformKey[];
  confidence: ConfidenceFilter;
  include_static: boolean;
}
