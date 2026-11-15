import { useCallback, useMemo, useState } from "react";
import { api } from "../api/client";
import { useAoi, useHotspots, useMeta, useRange, useReferenceStates, useSummary } from "../api/hooks";
import type { AreaGeometry, HotspotRequest, HotspotResponse, PlatformKey, RangeResponse } from "../api/types";
import { AreaPicker } from "../components/AreaPicker";
import { AXIS_STYLE, EChart, LEGEND_GRID_TOP, LEGEND_STYLE, TOOLTIP_STYLE, type ChartOption } from "../components/EChart";
import { MapView, type DrawMode, type MapFeatureCollection, type PopupRow } from "../components/MapView";
import { Card, describeError, ErrorBlock, Notice, QueryState, RampLegend, Segmented, Spinner, Stat, Swatch } from "../components/ui";
import {
  fireColor,
  fireRampCss,
  PLATFORM_COLORS,
  PLATFORM_ORDER,
  THEME,
  VALUE_TYPE_BY_CODE,
  VALUE_TYPE_COLORS,
  VALUE_TYPE_SHORT,
} from "../lib/colors";
import { fileSlug } from "../lib/download";
import {
  addDays,
  clampDate,
  formatAmount,
  formatCompact,
  formatDate,
  formatNumber,
  formatPercent,
  formatRatio,
  formatSignedPercent,
  separatedLabels,
  localSolarTime,
  ordinal,
  parseIsoDate,
  utcTime,
} from "../lib/format";
import { cellPolygon } from "../lib/geo";
import { useAppState } from "../state/AppState";
import { MAP_WINDOWS, MAX_MAP_WINDOW, type Basemap } from "../state/urlState";

const CONFIDENCE_LABEL = ["Low", "Nominal", "High"];
const FRP_COLOR_MAX = 60;

function daysBetween(from: string, to: string): number {
  return Math.round((parseIsoDate(to).getTime() - parseIsoDate(from).getTime()) / 86_400_000);
}

function toFeatures(
  data: HotspotResponse | undefined,
  colorBy: "sensor" | "frp",
  platformKeys: PlatformKey[],
): { points: MapFeatureCollection | null; cells: MapFeatureCollection | null; gridMax: number } {
  if (!data) return { points: null, cells: null, gridMax: 0 };
  if (data.mode === "points") {
    const features: MapFeatureCollection["features"] = [];
    for (let i = 0; i < data.count; i += 1) {
      const platform = platformKeys[data.platform[i] ?? 0] ?? "TERRA";
      const frp = data.frp[i] ?? 0;
      features.push({
        type: "Feature",
        geometry: { type: "Point", coordinates: [data.lon[i] ?? 0, data.lat[i] ?? 0] },
        properties: {
          color: colorBy === "sensor" ? PLATFORM_COLORS[platform] : fireColor(Math.max(frp, 0.6), FRP_COLOR_MAX),
          platform,
          frp,
          conf: data.conf[i] ?? 0,
          day: data.day[i] ?? 0,
          date: addDays(data.date_from, data.day_offset[i] ?? 0),
          time: data.time[i] ?? 0,
          scan: data.scan[i] ?? 0,
          track: data.track[i] ?? 0,
          ftype: data.ftype[i] ?? 0,
          lon: data.lon[i] ?? 0,
          lat: data.lat[i] ?? 0,
        },
      });
    }
    return { points: { type: "FeatureCollection", features }, cells: null, gridMax: 0 };
  }
  const sorted = [...data.n].sort((a, b) => a - b);
  const gridMax = sorted.length ? (sorted[Math.floor(sorted.length * 0.98)] ?? sorted[sorted.length - 1] ?? 1) : 1;
  const features: MapFeatureCollection["features"] = [];
  for (let i = 0; i < data.count; i += 1) {
    const n = data.n[i] ?? 0;
    features.push({
      type: "Feature",
      geometry: { type: "Polygon", coordinates: cellPolygon(data.west[i] ?? 0, data.south[i] ?? 0, data.step) },
      properties: { color: fireColor(Math.max(n, gridMax * 0.02), gridMax), n, frp: data.frp[i] ?? 0, step: data.step },
    });
  }
  return { points: null, cells: { type: "FeatureCollection", features }, gridMax };
}

/** The chosen period next to the same calendar dates in every other year of the record. */
function historyOption(data: RangeResponse): ChartOption | null {
  const c = data.comparison;
  if (!c.available || data.total === null) return null;
  const rows = [...c.history, { year: c.year_of_range, value: data.total }].sort((a, b) => a.year - b.year);
  const top = Math.max(...rows.map((r) => r.value ?? 0), c.median ?? 0, c.mean ?? 0);
  const [showMedian, showMean] = separatedLabels([c.median, c.mean], top);
  const lines = [
    ...(c.median !== null ? [{ yAxis: c.median, label: { formatter: "Median", show: showMedian } }] : []),
    ...(c.mean !== null ? [{ yAxis: c.mean, label: { formatter: "Average", show: showMean }, lineStyle: { type: "dotted" as const } }] : []),
  ];
  return {
    grid: { left: 62, right: 66, top: 16, bottom: 30 },
    tooltip: {
      ...TOOLTIP_STYLE,
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params: unknown) => {
        const row = rows[(params as { dataIndex: number }[])[0]?.dataIndex ?? 0];
        if (!row) return "";
        const against = c.mean !== null && c.mean > 0 && row.value !== null ? formatSignedPercent((row.value - c.mean) / c.mean) : null;
        return [
          `<strong>${row.year}</strong>`,
          `${formatAmount(row.value)} ${data.metric.unit_short} on the same dates`,
          ...(against ? [`${against} against the average of the other years`] : []),
        ].join("<br/>");
      },
    },
    xAxis: { type: "category", data: rows.map((r) => String(r.year)), ...AXIS_STYLE },
    yAxis: { type: "value", ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, formatter: (v: number) => formatCompact(v) } },
    series: [
      {
        type: "bar",
        barMaxWidth: 22,
        data: rows.map((r) => ({ value: r.value, itemStyle: { color: r.year === c.year_of_range ? THEME.fire : "#3b4756" } })),
        markLine: lines.length
          ? { silent: true, symbol: "none", lineStyle: { color: THEME.text, type: "dashed" }, label: { color: THEME.muted, fontSize: 10 }, data: lines }
          : undefined,
      },
    ],
  };
}

function rangeOption(data: RangeResponse): ChartOption {
  const s = data.series;
  const span = s.resolution === "daily" ? "per day" : s.resolution === "weekly" ? "per 7 days" : "per 30 days";
  return {
    grid: { left: 62, right: 18, top: LEGEND_GRID_TOP, bottom: 30 },
    legend: { ...LEGEND_STYLE, data: ["Harmonized activity", "Typical for the dates"] },
    tooltip: {
      ...TOOLTIP_STYLE,
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params: unknown) => {
        const i = (params as { dataIndex: number }[])[0]?.dataIndex ?? 0;
        const type = VALUE_TYPE_BY_CODE[s.vtype[i] ?? 0] ?? "missing";
        const value = s.values[i];
        const head = s.resolution === "daily" ? formatDate(s.labels[i]) : `From ${formatDate(s.labels[i])}`;
        const lines = [`<strong>${head}</strong>`];
        if (value === null || value === undefined) lines.push("No data for this period");
        else {
          lines.push(`${formatAmount(value)} ${data.metric.unit_short} (${VALUE_TYPE_SHORT[type].toLowerCase()})`);
          if (type !== "reference") lines.push(`80 percent interval: ${formatAmount(s.lower[i])} to ${formatAmount(s.upper[i])}`);
        }
        lines.push(`Typical: ${formatAmount(s.typical[i])}`);
        return lines.join("<br/>");
      },
    },
    xAxis: {
      type: "category",
      data: s.labels,
      ...AXIS_STYLE,
      axisLabel: { ...AXIS_STYLE.axisLabel, formatter: (d: string) => (s.resolution === "thirty_day" ? d.slice(0, 7) : formatDate(d, "short")) },
    },
    yAxis: { type: "value", name: `${data.metric.unit_short} ${span}`, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, formatter: (v: number) => formatCompact(v) } },
    series: [
      {
        name: "Harmonized activity",
        type: "bar",
        barCategoryGap: "18%",
        data: s.values.map((v, i) => ({ value: v, itemStyle: { color: VALUE_TYPE_COLORS[VALUE_TYPE_BY_CODE[s.vtype[i] ?? 0] ?? "missing"] } })),
        itemStyle: { color: THEME.cool },
      },
      { name: "Typical for the dates", type: "line", data: s.typical, symbol: "none", lineStyle: { color: THEME.text, width: 1.6 }, itemStyle: { color: THEME.text } },
    ],
  };
}

function RangeCard({
  from,
  to,
  sensors,
  includeStatic,
}: {
  from: string;
  to: string;
  sensors: PlatformKey[];
  includeStatic: boolean;
}) {
  const { state } = useAppState();
  const aoi = useAoi();
  const range = useRange(from, to);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "info" | "danger"; text: string } | null>(null);
  const option = useMemo(() => (range.data ? rangeOption(range.data) : null), [range.data]);

  const run = async (kind: string, action: () => Promise<{ rows: number | null }>) => {
    setBusy(kind);
    setMessage(null);
    try {
      const { rows } = await action();
      setMessage({ tone: "info", text: rows === null ? "The file was saved." : `Saved ${formatNumber(rows)} detections.` });
    } catch (error) {
      setMessage({ tone: "danger", text: describeError(error).detail });
    } finally {
      setBusy(null);
    }
  };

  const ref = aoi.ref;
  const history = useMemo(() => (range.data ? historyOption(range.data) : null), [range.data]);
  return (
    <Card
      title={`Activity from ${formatDate(from)} to ${formatDate(to)}`}
      subtitle="Harmonized burning activity for the date range chosen on the left, compared with the same calendar dates in every other year."
      actions={range.isFetching ? <Spinner label="Updating the date range" /> : undefined}
    >
      <QueryState query={range} loadingLabel="Adding up the date range" height={220}>
        {(d) => {
          const c = d.comparison;
          const estimated = d.days_by_type.modis_harmonized + d.days_by_type.viirs_calibrated;
          return (
            <>
              <div className="stat-grid">
                <Stat
                  label={`Total in ${formatNumber(d.range.days)} days`}
                  value={d.total === null ? "Withheld" : formatAmount(d.total)}
                  hint={
                    d.total === null
                      ? `${formatNumber(d.days_missing)} of the days have no data`
                      : estimated > 0
                        ? `${d.metric.unit_short}; 80 percent interval ${formatAmount(d.lower)} to ${formatAmount(d.upper)}`
                        : `${d.metric.unit_short}, all observed by VIIRS S-NPP`
                  }
                  tone="fire"
                />
                <Stat
                  label="Against the same dates in other years"
                  value={c.available && c.percentile !== null ? `${ordinal(c.percentile)} percentile` : "n/a"}
                  hint={
                    c.available
                      ? `${formatRatio(c.ratio_to_median)} the median and ${formatSignedPercent(c.anomaly_vs_mean)} against the average of ${c.reference_years} years; rank ${c.rank} of ${c.reference_years + 1}`
                      : c.reason
                  }
                />
                <Stat label="Average per day" value={formatAmount(d.mean_per_day)} hint={d.metric.unit_short} />
                <Stat
                  label="Highest day"
                  value={d.peak_day ? formatDate(d.peak_day.date) : "n/a"}
                  hint={d.peak_day ? `${formatAmount(d.peak_day.value)} ${d.metric.unit_short}` : undefined}
                />
              </div>
              {option && (
                <EChart
                  option={option}
                  height={260}
                  ariaLabel={`Harmonized burning activity from ${formatDate(from)} to ${formatDate(to)} for ${d.aoi.name}`}
                  downloadName={`agnisetu_range_${fileSlug(d.aoi.name)}_${from}_${to}`}
                />
              )}
              <div className="legend-row">
                {(["reference", "viirs_calibrated", "modis_harmonized", "missing"] as const).map((type) => (
                  <Swatch key={type} color={VALUE_TYPE_COLORS[type]} label={`${VALUE_TYPE_SHORT[type]}: ${formatNumber(d.days_by_type[type])} days`} />
                ))}
              </div>
              {history && c.available && (
                <>
                  <h3 className="chart-title">
                    {d.range.days === 1 ? `${formatDate(to, "short")} in every year` : `The same ${formatNumber(d.range.days)} days in every year`}
                  </h3>
                  <p className="muted small">
                    How this period compares with the same calendar dates in each year of the harmonized record. The
                    highlighted bar is the period chosen here. The dashed line is the median of the other years (
                    {formatCompact(c.median)}) and the dotted line is their average ({formatCompact(c.mean)}).
                  </p>
                  <EChart
                    option={history}
                    height={240}
                    ariaLabel={`Harmonized activity on the same calendar dates in every year for ${d.aoi.name}`}
                    downloadName={`agnisetu_same_dates_every_year_${fileSlug(d.aoi.name)}_${from}_${to}`}
                  />
                </>
              )}
            </>
          );
        }}
      </QueryState>
      <div className="toolbar range-actions">
        <button
          type="button"
          className="btn"
          disabled={!ref || busy !== null}
          onClick={() =>
            ref &&
            void run("hotspots", () =>
              api.downloadHotspots({
                aoi: ref,
                date_from: from,
                date_to: to,
                platforms: sensors,
                confidence: state.confidence,
                include_static: includeStatic,
              }),
            )
          }
        >
          Download detections in this range (CSV)
        </button>
        <button
          type="button"
          className="btn"
          disabled={!ref || busy !== null}
          onClick={() => ref && void run("daily", () => api.downloadDailyRange(ref, { metric: state.metric, confidence: state.confidence }, from, to))}
        >
          Download harmonized daily values (CSV)
        </button>
        {busy && <Spinner label="Preparing the file" />}
      </div>
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      <p className="muted small">
        The detections file lists every hotspot inside the area for these dates with position, time, sensor,
        confidence and fire power, using the sensor and confidence filters set on this page. Each row also carries a
        harmonized weight: the number of VIIRS S-NPP equivalent detections it stands for, so the weights of a day add
        up to the harmonized value of that day whichever sensor saw the fire.
      </p>
    </Card>
  );
}

export function ExploreTab() {
  const { state, update } = useAppState();
  const meta = useMeta();
  const aoi = useAoi();
  const summary = useSummary();
  const reference = useReferenceStates();
  const [drawMode, setDrawMode] = useState<DrawMode>("none");
  const [pending, setPending] = useState<AreaGeometry | null>(null);
  const [viewBox, setViewBox] = useState<[number, number, number, number] | null>(null);
  const [clip, setClip] = useState(true);
  const [includeStatic, setIncludeStatic] = useState(false);
  const [colorBy, setColorBy] = useState<"sensor" | "frp">("sensor");
  const [sensors, setSensors] = useState<Record<PlatformKey, boolean>>({
    TERRA: true, AQUA: true, SNPP: true, NOAA20: true, NOAA21: true,
  });

  const coverage = meta.data?.coverage;
  const recordStart = coverage?.record_start ?? "2000-11-01";
  const lastDay = coverage?.last_day ?? null;
  const defaultDate = coverage?.last_complete_day ?? null;
  const mapDate = state.mapDate && lastDay ? clampDate(state.mapDate, recordStart, lastDay) : defaultDate;
  const dateFrom = mapDate ? clampDate(addDays(mapDate, -(state.mapWindow - 1)), recordStart, mapDate) : null;
  const platformKeys = useMemo(() => (meta.data?.platforms ?? []).map((p) => p.key), [meta.data]);
  const activeSensors = useMemo(() => PLATFORM_ORDER.filter((k) => sensors[k]), [sensors]);

  const request = useMemo<HotspotRequest | null>(() => {
    if (!mapDate || !dateFrom) return null;
    const useClip = clip && aoi.ref !== null && aoi.info !== null;
    const bbox = useClip ? aoi.info?.bbox : viewBox;
    if (!bbox) return null;
    const rounded = bbox.map((v) => Number(v.toFixed(3))) as [number, number, number, number];
    if (!(rounded[0] < rounded[2] && rounded[1] < rounded[3])) return null;
    return {
      bbox: rounded,
      date_from: dateFrom,
      date_to: mapDate,
      platforms: activeSensors,
      confidence: state.confidence,
      include_static: includeStatic,
      clip_to_aoi: useClip,
      ...(useClip && aoi.ref ? { aoi: aoi.ref } : {}),
    };
  }, [mapDate, dateFrom, clip, aoi.ref, aoi.info, viewBox, activeSensors, state.confidence, includeStatic]);

  const hotspots = useHotspots(request, aoi.info?.id ?? null);
  const layers = useMemo(() => toFeatures(hotspots.data, colorBy, platformKeys), [hotspots.data, colorBy, platformKeys]);

  const describeFeature = useCallback((properties: Record<string, unknown>, layer: "points" | "cells") => {
    if (layer === "cells") {
      const rows: PopupRow[] = [
        { label: "Detections", value: formatNumber(Number(properties.n)) },
        { label: "Fire power", value: `${formatAmount(Number(properties.frp))} MW` },
        { label: "Cell size", value: `${Number(properties.step)} degrees` },
      ];
      return { title: "Density cell", rows };
    }
    const platform = String(properties.platform) as PlatformKey;
    const label = meta.data?.platforms.find((p) => p.key === platform)?.label ?? platform;
    const time = Number(properties.time);
    const lon = Number(properties.lon);
    const rows: PopupRow[] = [
      { label: "Date", value: formatDate(String(properties.date)) },
      { label: "Time", value: `${utcTime(time)} UTC, ${localSolarTime(time, lon)} local solar` },
      { label: "Fire power", value: `${formatAmount(Number(properties.frp))} MW` },
      { label: "Confidence", value: CONFIDENCE_LABEL[Number(properties.conf)] ?? "Unknown" },
      { label: "Overpass", value: Number(properties.day) === 1 ? "Day" : "Night" },
      { label: "Pixel", value: `${Number(properties.scan).toFixed(2)} by ${Number(properties.track).toFixed(2)} km` },
      { label: "Position", value: `${Number(properties.lat).toFixed(4)}, ${lon.toFixed(4)}` },
    ];
    if (Number(properties.ftype) !== 0) rows.push({ label: "Type", value: "Static heat source" });
    return { title: label, rows };
  }, [meta.data]);

  const shift = (days: number) => {
    if (!mapDate || !lastDay) return;
    update({ mapDate: clampDate(addDays(mapDate, days), recordStart, lastDay) });
  };

  /** Move the start of the range, keeping the end; the range is limited to one year. */
  const setFrom = (value: string) => {
    if (!mapDate || !value) return;
    const from = clampDate(value, recordStart, mapDate);
    update({ mapWindow: Math.min(MAX_MAP_WINDOW, Math.max(1, daysBetween(from, mapDate) + 1)) });
  };

  /** Move the end of the range, keeping the start where possible. */
  const setTo = (value: string) => {
    if (!value || !lastDay) return;
    const to = clampDate(value, recordStart, lastDay);
    if (!dateFrom || dateFrom > to) {
      // The new end is before the current start: slide the whole range, keeping its length.
      update({ mapDate: to });
      return;
    }
    update({ mapDate: to, mapWindow: Math.min(MAX_MAP_WINDOW, Math.max(1, daysBetween(dateFrom, to) + 1)) });
  };

  const data = hotspots.data;
  const areaSlug = fileSlug(aoi.info?.name ?? "area");

  return (
    <div className="explore">
      <aside className="side-panel" aria-label="Area and map controls">
        <Card title="Area of interest">
          <AreaPicker
            drawMode={drawMode}
            onDrawModeChange={setDrawMode}
            pendingGeometry={pending}
            onPendingHandled={() => setPending(null)}
          />
        </Card>

        <Card title="Date range and detections">
          <div className="control-stack">
            <div>
              <div className="range-row">
                <label>
                  <span className="control-label">From</span>
                  <input type="date" value={dateFrom ?? ""} min={recordStart} max={mapDate ?? undefined} onChange={(event) => setFrom(event.target.value)} />
                </label>
                <label>
                  <span className="control-label">To</span>
                  <input type="date" value={mapDate ?? ""} min={recordStart} max={lastDay ?? undefined} onChange={(event) => setTo(event.target.value)} />
                </label>
              </div>
              <div className="date-row range-shift">
                <button type="button" className="btn btn-icon" onClick={() => shift(-state.mapWindow)} aria-label="Earlier window">
                  &lt;
                </button>
                <span className="small muted">
                  {formatNumber(state.mapWindow)} {state.mapWindow === 1 ? "day" : "days"}, up to {MAX_MAP_WINDOW}
                </span>
                <button
                  type="button"
                  className="btn btn-icon"
                  onClick={() => shift(state.mapWindow)}
                  aria-label="Later window"
                  disabled={!mapDate || !lastDay || mapDate >= lastDay}
                >
                  &gt;
                </button>
              </div>
            </div>
            <Segmented
              label="Quick length"
              value={String(state.mapWindow)}
              onChange={(value) => update({ mapWindow: Number(value) })}
              options={MAP_WINDOWS.map((d) => ({ value: String(d), label: d === 1 ? "1 day" : `${d} days` }))}
            />
            <fieldset className="checks">
              <legend className="control-label">Sensors</legend>
              {PLATFORM_ORDER.map((key) => (
                <label key={key} className="check">
                  <input
                    type="checkbox"
                    checked={sensors[key]}
                    onChange={(event) => setSensors((previous) => ({ ...previous, [key]: event.target.checked }))}
                  />
                  <span className="check-dot" style={{ background: PLATFORM_COLORS[key] }} aria-hidden="true" />
                  <span>{meta.data?.platforms.find((p) => p.key === key)?.label ?? key}</span>
                </label>
              ))}
            </fieldset>
            <Segmented
              label="Color points by"
              value={colorBy}
              onChange={setColorBy}
              options={[
                { value: "sensor", label: "Sensor" },
                { value: "frp", label: "Fire power" },
              ]}
            />
            <Segmented<Basemap>
              label="Base map"
              value={state.basemap}
              onChange={(basemap) => update({ basemap })}
              options={[
                { value: "bluemarble", label: "Blue Marble", title: "NASA Blue Marble" },
                { value: "truecolor", label: "Satellite day", title: "NASA true color image for the end date of the range" },
                { value: "streets", label: "Streets", title: "OpenStreetMap" },
              ]}
            />
            <label className="check">
              <input type="checkbox" checked={clip} onChange={(event) => setClip(event.target.checked)} />
              <span>Only inside the selected area</span>
            </label>
            <label className="check">
              <input type="checkbox" checked={includeStatic} onChange={(event) => setIncludeStatic(event.target.checked)} />
              <span>Include static heat sources (industry, flares)</span>
            </label>
          </div>
        </Card>
      </aside>

      <div className="explore-main">
        <Card
          title={dateFrom && mapDate ? `Active fire detections, ${formatDate(dateFrom)} to ${formatDate(mapDate)}` : "Active fire detections"}
          subtitle={
            data
              ? data.mode === "points"
                ? `${formatNumber(data.count)} detections shown`
                : `${formatNumber(data.total_in_view)} detections, shown as a density grid of ${data.step} degree cells because they exceed the ${formatNumber(data.max_points)} point limit`
              : undefined
          }
          actions={hotspots.isFetching ? <Spinner label="Loading detections" /> : undefined}
        >
          <MapView
            basemap={state.basemap}
            imageryDate={mapDate}
            aoi={aoi.geometry}
            fitKey={aoi.info?.id ?? null}
            fitBounds={aoi.info?.bbox ?? null}
            points={layers.points}
            cells={layers.cells}
            reference={reference.data ?? null}
            drawMode={drawMode}
            onDrawComplete={(geometry) => {
              setDrawMode("none");
              setPending(geometry);
            }}
            onDrawCancel={() => setDrawMode("none")}
            onViewChange={setViewBox}
            describeFeature={describeFeature}
            ariaLabel="Map of active fire detections with the selected area outlined"
            height="min(62vh, 620px)"
            downloadName={dateFrom && mapDate ? `agnisetu_map_${areaSlug}_${dateFrom}_${mapDate}` : undefined}
          />
          <div className="legend-row">
            {data?.mode === "grid" ? (
              <RampLegend gradient={fireRampCss()} low="few" high={`${formatCompact(layers.gridMax)} or more per cell`} label="Detections" />
            ) : colorBy === "sensor" ? (
              activeSensors.map((key) => (
                <Swatch key={key} color={PLATFORM_COLORS[key]} label={meta.data?.platforms.find((p) => p.key === key)?.label ?? key} />
              ))
            ) : (
              <RampLegend gradient={fireRampCss()} low="under 1 MW" high={`${FRP_COLOR_MAX} MW or more`} label="Fire radiative power" />
            )}
          </div>
          {hotspots.isError && <ErrorBlock error={hotspots.error} onRetry={() => void hotspots.refetch()} compact />}
          {data && data.count === 0 && !hotspots.isFetching && (
            <Notice tone="info">
              No detections match these filters in this date range. Try a longer range, another date, or more sensors.
            </Notice>
          )}
          {state.basemap === "truecolor" && (
            <p className="muted small">
              Satellite day shows the NASA corrected reflectance image for {formatDate(mapDate)} (VIIRS NOAA-20 from 2018,
              MODIS Terra before). Smoke plumes and cloud are visible. Zoom is limited to the native 250 m imagery.
            </p>
          )}
        </Card>

        {dateFrom && mapDate && (
          <RangeCard from={dateFrom} to={mapDate} sensors={activeSensors} includeStatic={includeStatic} />
        )}

        <Card title="Record at a glance" subtitle={aoi.info ? aoi.info.name : undefined}>
          <QueryState query={summary} loadingLabel="Reading the full record of detections for this area" height={150}>
            {(s) => {
              const modis = s.platforms.filter((p) => p.instrument === "MODIS").reduce((a, p) => a + p.detections, 0);
              const viirs = s.platforms.filter((p) => p.instrument === "VIIRS").reduce((a, p) => a + p.detections, 0);
              const estimated = s.days_by_type.modis_harmonized + s.days_by_type.viirs_calibrated;
              const total = estimated + s.days_by_type.reference + s.days_by_type.missing;
              return (
                <>
                  <div className="stat-grid">
                    <Stat label="Record" value={`${s.record.start.slice(0, 4)} to ${s.record.end.slice(0, 4)}`} hint={`through ${formatDate(s.record.last_complete_day)}`} />
                    <Stat label="MODIS detections" value={formatCompact(modis)} hint="Terra and Aqua, 1 km" tone="fire" />
                    <Stat label="VIIRS detections" value={formatCompact(viirs)} hint="S-NPP, NOAA-20, NOAA-21, 375 m" tone="cool" />
                    <Stat
                      label="Harmonized annual mean"
                      value={formatCompact(s.harmonized_annual.mean)}
                      hint={`${s.metric.unit_short} per year over ${s.harmonized_annual.complete_years} complete years`}
                    />
                    <Stat
                      label="Peak year"
                      value={s.harmonized_annual.peak_year ?? "n/a"}
                      hint={`${formatCompact(s.harmonized_annual.peak_value)} ${s.metric.unit_short}`}
                    />
                    <Stat
                      label="Last 7 days"
                      value={formatAmount(s.latest.last_7_days)}
                      hint={`${s.metric.unit_short}, to ${formatDate(s.latest.date)}`}
                    />
                    <Stat
                      label="Static sources removed"
                      value={formatCompact(s.static_removed_total)}
                      hint={`${formatPercent(s.static_share, 1)} of all detections`}
                    />
                    <Stat
                      label="Days that are estimates"
                      value={formatPercent(total ? estimated / total : 0)}
                      hint={`${formatNumber(s.days_by_type.missing)} days with no sensor`}
                    />
                  </div>
                  <div className="legend-row">
                    {(["reference", "viirs_calibrated", "modis_harmonized", "missing"] as const).map((type) => (
                      <Swatch
                        key={type}
                        color={VALUE_TYPE_COLORS[type]}
                        label={`${VALUE_TYPE_SHORT[type]}: ${formatNumber(s.days_by_type[type])} days`}
                      />
                    ))}
                  </div>
                  <div className="table-scroll">
                    <table className="table">
                      <caption className="sr-only">Detections by sensor for the selected area</caption>
                      <thead>
                        <tr>
                          <th scope="col">Sensor</th>
                          <th scope="col" className="num">Detections</th>
                          <th scope="col" className="num">Fire power, MW</th>
                          <th scope="col" className="num">Static removed</th>
                          <th scope="col">First detection</th>
                          <th scope="col">Last detection</th>
                        </tr>
                      </thead>
                      <tbody>
                        {s.platforms.map((p) => (
                          <tr key={p.key}>
                            <th scope="row">
                              <span className="check-dot" style={{ background: PLATFORM_COLORS[p.key] }} aria-hidden="true" /> {p.label}
                            </th>
                            <td className="num">{formatNumber(p.detections)}</td>
                            <td className="num">{formatNumber(p.frp_mw)}</td>
                            <td className="num">{formatNumber(p.static_removed)}</td>
                            <td>{formatDate(p.first_detection)}</td>
                            <td>{formatDate(p.last_detection)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              );
            }}
          </QueryState>
        </Card>
      </div>
    </div>
  );
}
