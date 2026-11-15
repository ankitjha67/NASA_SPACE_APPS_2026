import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef } from "react";
import { resolveArea, useAppState } from "../state/AppState";
import { api, ApiError } from "./client";
import type { AoiInfo, AoiRef, AreaGeometry, HotspotRequest, Options, Resolution, SeriesKind } from "./types";

export function useMeta() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["meta"],
    queryFn: ({ signal }) => api.meta(signal),
    staleTime: 60_000,
    refetchInterval: 120_000,
  });
  // When the server ingests new data its store revision changes; drop every derived result.
  const revision = query.data?.coverage.revision;
  const seen = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (revision === undefined) return;
    if (seen.current !== undefined && seen.current !== revision) {
      void client.invalidateQueries({ predicate: (q) => !["meta", "presets", "preset", "reference-states"].includes(String(q.queryKey[0])) });
    }
    seen.current = revision;
  }, [revision, client]);
  return query;
}

export interface ResolvedAoi {
  ref: AoiRef | null;
  info: AoiInfo | null;
  geometry: AreaGeometry | null;
  isLoading: boolean;
  error: ApiError | Error | null;
  missingCustom: boolean;
}

/** Resolve the area in the URL (preset id or stored custom geometry) through the server. */
export function useAoi(): ResolvedAoi {
  const { state } = useAppState();
  const meta = useMeta();
  const presetId = state.presetId ?? (state.customKey ? null : (meta.data?.default_preset ?? null));
  const stored = useMemo(() => (state.customKey ? resolveArea(state.customKey) : null), [state.customKey]);

  const preset = useQuery({
    queryKey: ["preset", presetId],
    queryFn: ({ signal }) => api.preset(presetId as string, signal),
    enabled: presetId !== null,
    staleTime: Infinity,
  });
  const custom = useQuery({
    queryKey: ["custom-area", state.customKey],
    queryFn: ({ signal }) => api.validateAoi((stored as NonNullable<typeof stored>).geometry, stored?.name ?? "Custom area", signal),
    enabled: state.customKey !== null && stored !== null,
    staleTime: Infinity,
  });

  return useMemo<ResolvedAoi>(() => {
    if (state.customKey) {
      if (!stored) {
        return { ref: null, info: null, geometry: null, isLoading: false, error: null, missingCustom: true };
      }
      const data = custom.data;
      return {
        ref: data ? { geometry: data.geometry, name: data.aoi.name } : null,
        info: data?.aoi ?? null,
        geometry: data?.geometry ?? null,
        isLoading: custom.isLoading,
        error: custom.error,
        missingCustom: false,
      };
    }
    const data = preset.data;
    return {
      ref: data ? { preset_id: data.id } : null,
      info: data?.aoi ?? null,
      geometry: data?.geometry ?? null,
      isLoading: preset.isLoading || (presetId === null && meta.isLoading),
      error: preset.error ?? meta.error,
      missingCustom: false,
    };
  }, [state.customKey, stored, custom.data, custom.isLoading, custom.error, preset.data, preset.isLoading, preset.error, presetId, meta.isLoading, meta.error]);
}

export function useOptions(): Options {
  const { state } = useAppState();
  return useMemo(() => ({ metric: state.metric, confidence: state.confidence }), [state.metric, state.confidence]);
}

function useAnalysisKey(): { aoi: ResolvedAoi; options: Options; key: (string | number | null)[]; enabled: boolean } {
  const aoi = useAoi();
  const options = useOptions();
  const meta = useMeta();
  const ready = meta.data?.coverage.ready ?? false;
  return {
    aoi,
    options,
    key: [aoi.info?.id ?? null, options.metric, options.confidence],
    enabled: aoi.ref !== null && ready,
  };
}

export function useSummary() {
  const { aoi, options, key, enabled } = useAnalysisKey();
  return useQuery({
    queryKey: ["summary", ...key],
    queryFn: ({ signal }) => api.summary(aoi.ref as AoiRef, options, signal),
    enabled,
  });
}

export function useCalendar(series: SeriesKind, resolution: Resolution) {
  const { aoi, options, key, enabled } = useAnalysisKey();
  const { state } = useAppState();
  return useQuery({
    queryKey: ["calendar", ...key, series, resolution, state.baseline?.start ?? null, state.baseline?.end ?? null],
    queryFn: ({ signal }) => api.calendar(aoi.ref as AoiRef, options, series, resolution, state.baseline, signal),
    enabled,
    placeholderData: keepPreviousData,
  });
}

export function useHarmonization() {
  const { aoi, options, key, enabled } = useAnalysisKey();
  return useQuery({
    queryKey: ["harmonization", ...key],
    queryFn: ({ signal }) => api.harmonization(aoi.ref as AoiRef, options, signal),
    enabled,
  });
}

export function useAnomalies() {
  const { aoi, options, key, enabled } = useAnalysisKey();
  const { state } = useAppState();
  return useQuery({
    queryKey: ["anomalies", ...key, state.baseline?.start ?? null, state.baseline?.end ?? null, state.periodId],
    queryFn: ({ signal }) => api.anomalies(aoi.ref as AoiRef, options, state.baseline, state.periodId, signal),
    enabled,
    placeholderData: keepPreviousData,
  });
}

export function useWarning() {
  const { aoi, options, key, enabled } = useAnalysisKey();
  const { state } = useAppState();
  return useQuery({
    queryKey: ["warning", ...key, state.asOf],
    queryFn: ({ signal }) => api.warning(aoi.ref as AoiRef, options, state.asOf, signal),
    enabled,
    placeholderData: keepPreviousData,
  });
}

export function useRange(from: string | null, to: string | null) {
  const { aoi, options, key, enabled } = useAnalysisKey();
  return useQuery({
    queryKey: ["range", ...key, from, to],
    queryFn: ({ signal }) => api.range(aoi.ref as AoiRef, options, from as string, to as string, signal),
    enabled: enabled && from !== null && to !== null && from <= to,
    placeholderData: keepPreviousData,
  });
}

export function useRatioGrid(active: boolean) {
  const { aoi, options, key, enabled } = useAnalysisKey();
  return useQuery({
    queryKey: ["ratio-grid", ...key],
    queryFn: ({ signal }) => api.ratioGrid(aoi.ref as AoiRef, options, signal),
    enabled: enabled && active,
  });
}

export function useHotspots(request: HotspotRequest | null, aoiId: string | null) {
  const meta = useMeta();
  const ready = meta.data?.coverage.ready ?? false;
  return useQuery({
    queryKey: ["hotspots", request ? { ...request, aoi: undefined } : null, request?.clip_to_aoi ? aoiId : null],
    queryFn: ({ signal }) => api.hotspots(request as HotspotRequest, signal),
    enabled: request !== null && ready,
    placeholderData: keepPreviousData,
    staleTime: 5 * 60_000,
  });
}

export function useDataCoverage() {
  return useQuery({
    queryKey: ["data-coverage"],
    queryFn: ({ signal }) => api.dataCoverage(signal),
    staleTime: 30_000,
  });
}

export function useReferenceStates() {
  return useQuery({
    queryKey: ["reference-states"],
    queryFn: ({ signal }) => api.referenceStates(signal),
    staleTime: Infinity,
    gcTime: Infinity,
  });
}

export function usePresetSearch(query: string) {
  return useQuery({
    queryKey: ["presets", query],
    queryFn: ({ signal }) => api.presets(query, signal),
    staleTime: Infinity,
    placeholderData: keepPreviousData,
  });
}
