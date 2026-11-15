import type {
  AnomaliesResponse,
  AoiRef,
  ApiErrorBody,
  AreaGeometry,
  CalendarResponse,
  DataCoverageResponse,
  HarmonizationResponse,
  HotspotExportRequest,
  HotspotRequest,
  HotspotResponse,
  Job,
  Meta,
  Options,
  PresetDetail,
  PresetEntry,
  RangeResponse,
  RatioGridResponse,
  ReferenceStates,
  Resolution,
  SeriesKind,
  SummaryResponse,
  ValidatedAoi,
  WarningResponse,
} from "./types";

import { saveBlob } from "../lib/download";

const BASE = "/api/v1";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;

  constructor(status: number, code: string, message: string, requestId: string | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

async function toError(response: Response): Promise<ApiError> {
  let code = "http_error";
  let message = `The server answered with status ${response.status}.`;
  let requestId = response.headers.get("x-request-id");
  try {
    const body = (await response.json()) as Partial<ApiErrorBody>;
    if (body.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      requestId = body.error.request_id ?? requestId;
    }
  } catch {
    // Non JSON error body (for example from a proxy). Keep the generic message.
  }
  if (response.status === 429) {
    const retry = response.headers.get("retry-after");
    message = retry ? `Too many requests. Try again in ${retry} seconds.` : message;
  }
  return new ApiError(response.status, code, message, requestId);
}

async function request(path: string, init: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, { ...init, credentials: "same-origin" });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, "network_error", "The server could not be reached. Check that it is running.", null);
  }
  if (!response.ok) throw await toError(response);
  return response;
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await request(path, { method: "GET", signal, headers: { Accept: "application/json" } });
  return (await response.json()) as T;
}

async function postJson<T>(path: string, body: unknown, signal?: AbortSignal, headers?: Record<string, string>): Promise<T> {
  const response = await request(path, {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json", Accept: "application/json", ...headers },
    body: JSON.stringify(body),
  });
  return (await response.json()) as T;
}

export interface Baseline {
  start: number;
  end: number;
}

function baselineFields(baseline: Baseline | null): Record<string, number> {
  return baseline ? { baseline_start: baseline.start, baseline_end: baseline.end } : {};
}

export const api = {
  meta: (signal?: AbortSignal) => getJson<Meta>("/meta", signal),

  presets: (query: string, signal?: AbortSignal) =>
    getJson<{ items: PresetEntry[] }>(`/presets?q=${encodeURIComponent(query)}&limit=40`, signal),

  preset: (id: string, signal?: AbortSignal) => getJson<PresetDetail>(`/presets/${encodeURI(id)}`, signal),

  referenceStates: (signal?: AbortSignal) => getJson<ReferenceStates>("/reference/states", signal),

  validateAoi: (geometry: AreaGeometry, name: string, signal?: AbortSignal) =>
    postJson<ValidatedAoi>("/aoi/validate", { geometry, name }, signal),

  summary: (aoi: AoiRef, options: Options, signal?: AbortSignal) =>
    postJson<SummaryResponse>("/analysis/summary", { aoi, options }, signal),

  calendar: (
    aoi: AoiRef,
    options: Options,
    series: SeriesKind,
    resolution: Resolution,
    baseline: Baseline | null,
    signal?: AbortSignal,
  ) =>
    postJson<CalendarResponse>(
      "/analysis/calendar",
      { aoi, options, series, resolution, ...baselineFields(baseline) },
      signal,
    ),

  harmonization: (aoi: AoiRef, options: Options, signal?: AbortSignal) =>
    postJson<HarmonizationResponse>("/analysis/harmonization", { aoi, options }, signal),

  anomalies: (aoi: AoiRef, options: Options, baseline: Baseline | null, periodId: string | null, signal?: AbortSignal) =>
    postJson<AnomaliesResponse>(
      "/analysis/anomalies",
      { aoi, options, ...baselineFields(baseline), ...(periodId ? { period_id: periodId } : {}) },
      signal,
    ),

  warning: (aoi: AoiRef, options: Options, asOf: string | null, signal?: AbortSignal) =>
    postJson<WarningResponse>("/analysis/warning", { aoi, options, ...(asOf ? { as_of: asOf } : {}) }, signal),

  hotspots: (body: HotspotRequest, signal?: AbortSignal) => postJson<HotspotResponse>("/map/hotspots", body, signal),

  range: (aoi: AoiRef, options: Options, from: string, to: string, signal?: AbortSignal) =>
    postJson<RangeResponse>("/analysis/range", { aoi, options, date_from: from, date_to: to }, signal),

  ratioGrid: (aoi: AoiRef, options: Options, signal?: AbortSignal) =>
    postJson<RatioGridResponse>("/map/ratio-grid", { aoi, options }, signal),

  dataCoverage: (signal?: AbortSignal) => getJson<DataCoverageResponse>("/data/coverage", signal),

  /** POST to an export route and save the answer under the file name the server chose. */
  async downloadFrom(path: string, body: unknown, fallbackName: string): Promise<{ rows: number | null }> {
    const response = await request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const blob = await response.blob();
    const disposition = response.headers.get("content-disposition") ?? "";
    const match = /filename="([^"]+)"/.exec(disposition);
    saveBlob(blob, match?.[1] ?? fallbackName);
    const rows = response.headers.get("x-row-count");
    return { rows: rows === null ? null : Number(rows) };
  },

  download(kind: "daily.csv" | "calendar.csv" | "report.json", aoi: AoiRef, options: Options): Promise<{ rows: number | null }> {
    return api.downloadFrom(`/export/${kind}`, { aoi, options }, `agnisetu_${kind}`);
  },

  downloadDailyRange: (aoi: AoiRef, options: Options, from: string, to: string) =>
    api.downloadFrom("/export/daily.csv", { aoi, options, date_from: from, date_to: to }, "agnisetu_daily.csv"),

  downloadHotspots: (body: HotspotExportRequest) => api.downloadFrom("/export/hotspots.csv", body, "agnisetu_hotspots.csv"),

  adminRefresh: (token: string) =>
    postJson<{ job: Job }>("/admin/refresh-nrt", {}, undefined, { "X-Admin-Token": token }),

  adminBackfill: (token: string, start: string | null, end: string | null) =>
    postJson<{ job: Job }>(
      "/admin/backfill",
      { ...(start ? { start } : {}), ...(end ? { end } : {}) },
      undefined,
      { "X-Admin-Token": token },
    ),

  async adminJobs(token: string): Promise<{ jobs: Job[] }> {
    const response = await request("/admin/jobs", { method: "GET", headers: { "X-Admin-Token": token } });
    return (await response.json()) as { jobs: Job[] };
  },
};
