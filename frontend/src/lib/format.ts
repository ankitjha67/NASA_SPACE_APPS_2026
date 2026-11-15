const MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTHS_LONG = [
  "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
  "December",
];

const integer = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const oneDecimal = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });

export const DASH = "n/a";

export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  if (digits === 0) return integer.format(Math.round(value));
  return new Intl.NumberFormat("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
}

/** Activity amounts: whole numbers once large, one decimal when small. */
export function formatAmount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return Math.abs(value) >= 100 ? integer.format(Math.round(value)) : oneDecimal.format(value);
}

export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  if (Math.abs(value) >= 1000) return compact.format(value);
  return Number.isInteger(value) ? integer.format(value) : formatAmount(value);
}

/**
 * Which reference line labels can be drawn at the end of their lines without touching each other.
 * Values are given in order of importance. A label is kept when it lies at least `minShare` of the
 * axis top away from every label already kept.
 */
export function separatedLabels(values: (number | null | undefined)[], top: number, minShare = 0.08): boolean[] {
  const kept: number[] = [];
  return values.map((value) => {
    if (value === null || value === undefined || !Number.isFinite(value) || !(top > 0)) return false;
    if (kept.some((other) => Math.abs(other - value) < top * minShare)) return false;
    kept.push(value);
    return true;
  });
}

export function formatPercent(fraction: number | null | undefined, digits = 0): string {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) return DASH;
  return `${(fraction * 100).toFixed(digits)}%`;
}

export function formatSignedPercent(fraction: number | null | undefined, digits = 0): string {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) return DASH;
  const value = fraction * 100;
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

export function formatRatio(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return `${value.toFixed(value >= 10 ? 0 : value >= 1 ? 1 : 2)}x`;
}

export function ordinal(value: number): string {
  const rounded = Math.round(value);
  const mod100 = rounded % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${rounded}th`;
  const suffix = ["th", "st", "nd", "rd"][rounded % 10] ?? "th";
  return `${rounded}${suffix}`;
}

export function formatBytes(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  if (bytes >= 1e3) return `${(bytes / 1e3).toFixed(0)} KB`;
  return `${bytes} B`;
}

export function formatArea(km2: number): string {
  return `${integer.format(Math.round(km2))} sq km`;
}

/** Parse an ISO calendar date as a UTC date so no time zone can shift it. */
/** Name of a latitude and longitude box, with hemisphere letters in place of minus signs. */
export function boxName(west: number, south: number, east: number, north: number): string {
  const span = (low: number, high: number, positive: string, negative: string) => {
    if (low >= 0 && high >= 0) return `${low} to ${high} ${positive}`;
    if (low <= 0 && high <= 0) return `${Math.abs(high)} to ${Math.abs(low)} ${negative}`;
    return `${Math.abs(low)} ${negative} to ${high} ${positive}`;
  };
  return `Box ${span(south, north, "N", "S")}, ${span(west, east, "E", "W")}`;
}

export function parseIsoDate(text: string): Date {
  const [y, m, d] = text.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1));
}

export function toIsoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function addDays(iso: string, days: number): string {
  const date = parseIsoDate(iso);
  date.setUTCDate(date.getUTCDate() + days);
  return toIsoDate(date);
}

export function clampDate(iso: string, min: string, max: string): string {
  if (iso < min) return min;
  if (iso > max) return max;
  return iso;
}

export function formatDate(iso: string | null | undefined, style: "short" | "long" | "medium" = "medium"): string {
  if (!iso) return DASH;
  const date = parseIsoDate(iso);
  const day = date.getUTCDate();
  const month = date.getUTCMonth();
  const year = date.getUTCFullYear();
  if (style === "short") return `${MONTHS_SHORT[month]} ${day}`;
  if (style === "long") return `${MONTHS_LONG[month]} ${day}, ${year}`;
  return `${MONTHS_SHORT[month]} ${day}, ${year}`;
}

export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return DASH;
  const hh = String(date.getUTCHours()).padStart(2, "0");
  const mm = String(date.getUTCMinutes()).padStart(2, "0");
  return `${formatDate(iso.slice(0, 10))} ${hh}:${mm} UTC`;
}

/** Day of year (1 based) to a short label in a non leap reference year. */
export function doyLabel(doy: number): string {
  const date = new Date(Date.UTC(2023, 0, 1));
  date.setUTCDate(doy);
  return `${MONTHS_SHORT[date.getUTCMonth()]} ${date.getUTCDate()}`;
}

/** Column positions where each month begins, for a 52 bin or 366 day axis. */
export function monthTicks(columns: number): { index: number; label: string }[] {
  const starts = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335];
  return starts.map((doy, i) => ({
    index: columns === 52 ? Math.min(51, Math.floor((doy - 1) / 7)) : doy - 1,
    label: MONTHS_SHORT[i] ?? "",
  }));
}

/** HHMM UTC plus longitude to local solar time text. */
export function localSolarTime(hhmm: number, lon: number): string {
  const minutes = Math.floor(hhmm / 100) * 60 + (hhmm % 100) + Math.round(lon * 4);
  const wrapped = ((minutes % 1440) + 1440) % 1440;
  return `${String(Math.floor(wrapped / 60)).padStart(2, "0")}:${String(wrapped % 60).padStart(2, "0")}`;
}

export function utcTime(hhmm: number): string {
  return `${String(Math.floor(hhmm / 100)).padStart(2, "0")}:${String(hhmm % 100).padStart(2, "0")}`;
}
