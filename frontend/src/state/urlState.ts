import type { ConfidenceFilter, Metric, Resolution, SeriesKind } from "../api/types";
import type { RampKey } from "../lib/colors";

export type TabKey = "explore" | "calendar" | "harmonization" | "anomalies" | "warning" | "data";
export type Basemap = "bluemarble" | "truecolor" | "streets";

export const TABS: { key: TabKey; label: string }[] = [
  { key: "explore", label: "Explore" },
  { key: "calendar", label: "Calendar" },
  { key: "harmonization", label: "Harmonization" },
  { key: "anomalies", label: "Anomalies" },
  { key: "warning", label: "Early Warning" },
  { key: "data", label: "Data and Methods" },
];

export const MAP_WINDOWS = [1, 3, 7, 14, 30] as const;
export const MAX_MAP_WINDOW = 366;

export interface AppState {
  tab: TabKey;
  presetId: string | null;
  customKey: string | null;
  metric: Metric;
  confidence: ConfidenceFilter;
  series: SeriesKind;
  resolution: Resolution;
  ramp: RampKey;
  baseline: { start: number; end: number } | null;
  year: number | null;
  periodId: string | null;
  asOf: string | null;
  mapDate: string | null;
  mapWindow: number;
  basemap: Basemap;
}

export const DEFAULT_STATE: AppState = {
  tab: "explore",
  presetId: null,
  customKey: null,
  metric: "count",
  confidence: "nominal",
  series: "harmonized",
  resolution: "weekly",
  ramp: "fire",
  baseline: null,
  year: null,
  periodId: null,
  asOf: null,
  mapDate: null,
  mapWindow: 7,
  basemap: "bluemarble",
};

const PRESET_RE = /^[a-z0-9]+(:[A-Za-z0-9-]+){1,3}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const KEY_RE = /^[a-z0-9]{4,32}$/;

function oneOf<T extends string>(value: string | null, allowed: readonly T[]): T | null {
  return value !== null && (allowed as readonly string[]).includes(value) ? (value as T) : null;
}

function year(value: string | null): number | null {
  if (value === null || !/^\d{4}$/.test(value)) return null;
  const n = Number(value);
  return n >= 2000 && n <= 2100 ? n : null;
}

/** Read application state from a query string, ignoring anything malformed. */
export function decodeState(search: string): AppState {
  const q = new URLSearchParams(search);
  const state: AppState = { ...DEFAULT_STATE };
  state.tab = oneOf(q.get("tab"), TABS.map((t) => t.key)) ?? DEFAULT_STATE.tab;
  const area = q.get("area");
  if (area && area.length <= 120 && PRESET_RE.test(area)) state.presetId = area;
  const custom = q.get("custom");
  if (!state.presetId && custom && KEY_RE.test(custom)) state.customKey = custom;
  state.metric = oneOf(q.get("m"), ["count", "frp"] as const) ?? DEFAULT_STATE.metric;
  state.confidence = oneOf(q.get("cf"), ["all", "nominal", "high"] as const) ?? DEFAULT_STATE.confidence;
  state.series = oneOf(q.get("s"), ["harmonized", "modis", "snpp", "noaa20"] as const) ?? DEFAULT_STATE.series;
  state.resolution = oneOf(q.get("r"), ["weekly", "monthly", "daily"] as const) ?? DEFAULT_STATE.resolution;
  state.ramp = oneOf(q.get("cr"), ["fire", "viridis", "cividis", "gray"] as const) ?? DEFAULT_STATE.ramp;
  const bs = year(q.get("bs"));
  const be = year(q.get("be"));
  if (bs !== null && be !== null && be - bs >= 4) state.baseline = { start: bs, end: be };
  state.year = year(q.get("y"));
  const period = q.get("pd");
  if (period && /^p\d{1,2}$/.test(period)) state.periodId = period;
  const asOf = q.get("d");
  if (asOf && DATE_RE.test(asOf)) state.asOf = asOf;
  const mapDate = q.get("md");
  if (mapDate && DATE_RE.test(mapDate)) state.mapDate = mapDate;
  const mapWindow = Number(q.get("mw"));
  if (Number.isInteger(mapWindow) && mapWindow >= 1 && mapWindow <= MAX_MAP_WINDOW) state.mapWindow = mapWindow;
  state.basemap = oneOf(q.get("bm"), ["bluemarble", "truecolor", "streets"] as const) ?? DEFAULT_STATE.basemap;
  return state;
}

/** Write state to a query string, leaving out defaults so links stay short. */
export function encodeState(state: AppState): string {
  const q = new URLSearchParams();
  if (state.tab !== DEFAULT_STATE.tab) q.set("tab", state.tab);
  if (state.presetId) q.set("area", state.presetId);
  else if (state.customKey) q.set("custom", state.customKey);
  if (state.metric !== DEFAULT_STATE.metric) q.set("m", state.metric);
  if (state.confidence !== DEFAULT_STATE.confidence) q.set("cf", state.confidence);
  if (state.series !== DEFAULT_STATE.series) q.set("s", state.series);
  if (state.resolution !== DEFAULT_STATE.resolution) q.set("r", state.resolution);
  if (state.ramp !== DEFAULT_STATE.ramp) q.set("cr", state.ramp);
  if (state.baseline) {
    q.set("bs", String(state.baseline.start));
    q.set("be", String(state.baseline.end));
  }
  if (state.year !== null) q.set("y", String(state.year));
  if (state.periodId) q.set("pd", state.periodId);
  if (state.asOf) q.set("d", state.asOf);
  if (state.mapDate) q.set("md", state.mapDate);
  if (state.mapWindow !== DEFAULT_STATE.mapWindow) q.set("mw", String(state.mapWindow));
  if (state.basemap !== DEFAULT_STATE.basemap) q.set("bm", state.basemap);
  const text = q.toString();
  return text ? `?${text}` : "";
}
