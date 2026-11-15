import type { AlertLevel, AnomalyClass, PlatformKey, ValueType } from "../api/types";

export const THEME = {
  bg: "#0e1116",
  surface: "#151a21",
  surface2: "#1b222b",
  border: "#2a3441",
  grid: "#232c38",
  text: "#e6edf3",
  muted: "#9aa7b4",
  faint: "#6b7785",
  fire: "#ff8a3d",
  cool: "#4fb3ff",
  ok: "#3fb68b",
  warn: "#f2c14e",
  danger: "#e5484d",
  missing: "#262e39",
} as const;

// Perceptually ordered ramp for activity on a dark background: dark plum to pale yellow.
const FIRE_STOPS: [number, number, number][] = [
  [27, 31, 41],
  [58, 28, 62],
  [108, 30, 82],
  [160, 40, 70],
  [208, 70, 48],
  [240, 122, 36],
  [249, 178, 60],
  [251, 229, 138],
  [255, 251, 224],
];

function interpolate(stops: [number, number, number][], t: number): string {
  const clamped = Math.min(1, Math.max(0, t));
  const scaled = clamped * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(scaled));
  const f = scaled - i;
  const a = stops[i] ?? [0, 0, 0];
  const b = stops[i + 1] ?? a;
  const mix = (k: 0 | 1 | 2) => Math.round(a[k] + (b[k] - a[k]) * f);
  return `rgb(${mix(0)}, ${mix(1)}, ${mix(2)})`;
}

/** Color for an activity value with a square root stretch against the scale maximum. */
export function fireColor(value: number, max: number): string {
  return rampColor("fire", value, max);
}

export function fireRampCss(): string {
  return rampCss("fire");
}

export type RampKey = "fire" | "viridis" | "cividis" | "gray";

/**
 * Calendar color ramps. Every one rises steadily in lightness, so the order of values survives color vision
 * deficiency and a grayscale print. Cividis was designed to look the same with and without red green deficiency.
 */
export const RAMPS: Record<RampKey, { label: string; note: string; stops: [number, number, number][] }> = {
  fire: { label: "Fire", note: "Dark plum to pale yellow", stops: FIRE_STOPS },
  viridis: {
    label: "Viridis",
    note: "Purple to green to yellow",
    stops: [
      [68, 1, 84],
      [71, 44, 122],
      [59, 81, 139],
      [44, 113, 142],
      [33, 144, 141],
      [39, 173, 129],
      [92, 200, 99],
      [170, 220, 50],
      [253, 231, 37],
    ],
  },
  cividis: {
    label: "Cividis",
    note: "Blue to yellow, made for color vision deficiency",
    stops: [
      [0, 32, 77],
      [0, 52, 110],
      [57, 72, 107],
      [87, 93, 109],
      [112, 113, 115],
      [138, 134, 120],
      [166, 157, 117],
      [195, 179, 105],
      [225, 204, 85],
      [255, 234, 70],
    ],
  },
  gray: {
    label: "Gray",
    note: "Dark to light, for print",
    stops: [
      [28, 32, 38],
      [70, 74, 80],
      [112, 116, 122],
      [154, 158, 164],
      [196, 200, 205],
      [245, 246, 248],
    ],
  },
};

export const RAMP_ORDER: RampKey[] = ["fire", "viridis", "cividis", "gray"];

export function rampColor(ramp: RampKey, value: number, max: number): string {
  const stops = RAMPS[ramp].stops;
  if (!(max > 0) || !(value > 0)) return interpolate(stops, 0);
  return interpolate(stops, Math.sqrt(Math.min(value, max) / max));
}

export function rampCss(ramp: RampKey): string {
  const stops = RAMPS[ramp].stops;
  const parts = stops.map((c, i) => `rgb(${c[0]}, ${c[1]}, ${c[2]}) ${(i / (stops.length - 1)) * 100}%`);
  return `linear-gradient(to right, ${parts.join(", ")})`;
}

const RATIO_STOPS: [number, number, number][] = [
  [35, 60, 110],
  [60, 130, 180],
  [120, 190, 190],
  [230, 220, 150],
  [240, 150, 70],
  [215, 60, 50],
];

export function ratioColor(ratio: number, low: number, high: number): string {
  const t = high > low ? (ratio - low) / (high - low) : 0.5;
  return interpolate(RATIO_STOPS, t);
}

export function ratioRampCss(): string {
  const parts = RATIO_STOPS.map((c, i) => `rgb(${c[0]}, ${c[1]}, ${c[2]}) ${(i / (RATIO_STOPS.length - 1)) * 100}%`);
  return `linear-gradient(to right, ${parts.join(", ")})`;
}

export const PLATFORM_COLORS: Record<PlatformKey, string> = {
  TERRA: "#f2c14e",
  AQUA: "#ff8a3d",
  SNPP: "#4fb3ff",
  NOAA20: "#8f9bff",
  NOAA21: "#3fd0c0",
};

export const PLATFORM_ORDER: PlatformKey[] = ["TERRA", "AQUA", "SNPP", "NOAA20", "NOAA21"];

export const VALUE_TYPE_COLORS: Record<ValueType, string> = {
  reference: "#4fb3ff",
  viirs_calibrated: "#8f9bff",
  modis_harmonized: "#ff8a3d",
  missing: "#3a4452",
};

export const VALUE_TYPE_LABELS: Record<ValueType, string> = {
  reference: "Observed by VIIRS S-NPP (reference)",
  viirs_calibrated: "VIIRS NOAA-20, calibrated to S-NPP",
  modis_harmonized: "Harmonized estimate from MODIS",
  missing: "No sensor coverage",
};

export const VALUE_TYPE_SHORT: Record<ValueType, string> = {
  reference: "Observed",
  viirs_calibrated: "Cross calibrated",
  modis_harmonized: "Harmonized estimate",
  missing: "No coverage",
};

export const VALUE_TYPE_BY_CODE: ValueType[] = ["missing", "reference", "viirs_calibrated", "modis_harmonized"];

export const ANOMALY_COLORS: Record<AnomalyClass, string> = {
  far_below: "#2f7fc1",
  below: "#7fb6d9",
  near_normal: "#4a5563",
  above: "#f2a65a",
  far_above: "#e2543b",
};

export const ANOMALY_ORDER: AnomalyClass[] = ["far_below", "below", "near_normal", "above", "far_above"];

export const LEVEL_COLORS: Record<AlertLevel, string> = {
  minimal: "#5d6875",
  below_normal: "#4fb3ff",
  normal: "#3fb68b",
  elevated: "#f2c14e",
  high: "#f08a3c",
  extreme: "#e5484d",
};

export const COVERAGE_COLORS: Record<string, string> = {
  pre_launch: "transparent",
  not_ingested: "#3a4452",
  outage: "#e5484d",
  observed: "#3fb68b",
  provisional: "#f2c14e",
};

export const COVERAGE_LABELS: Record<string, string> = {
  pre_launch: "Before launch",
  not_ingested: "Not ingested",
  outage: "Outage detected",
  observed: "Observed",
  provisional: "Provisional (partial day)",
};
