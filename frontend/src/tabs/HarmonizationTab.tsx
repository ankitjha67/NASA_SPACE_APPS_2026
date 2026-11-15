import { useCallback, useMemo, useState } from "react";
import { useAoi, useHarmonization, useRatioGrid, useReferenceStates } from "../api/hooks";
import type { AreaGeometry, CoverageSegment, HarmonizationResponse, PredictorSet, RatioGridResponse } from "../api/types";
import { AXIS_STYLE, EChart, LEGEND_GRID_TOP, LEGEND_STYLE, TOOLTIP_STYLE, type ChartOption } from "../components/EChart";
import { MapView, type MapFeatureCollection, type PopupRow } from "../components/MapView";
import { Card, EmptyBlock, Notice, QueryState, RampLegend, Segmented, Stat, Swatch } from "../components/ui";
import { COVERAGE_COLORS, COVERAGE_LABELS, PLATFORM_COLORS, ratioColor, ratioRampCss, THEME, VALUE_TYPE_COLORS } from "../lib/colors";
import { formatAmount, formatCompact, formatDate, formatNumber, formatPercent, formatRatio, formatSignedPercent, monthTicks, parseIsoDate } from "../lib/format";
import { fileSlug } from "../lib/download";
import { cellPolygon } from "../lib/geo";
import { useAppState } from "../state/AppState";
import type { Basemap } from "../state/urlState";

type SetKey = PredictorSet["key"];

function annualOption(data: HarmonizationResponse): ChartOption {
  const a = data.annual;
  const band = a.harmonized_upper.map((v, i) => (v === null || a.harmonized_lower[i] === null || a.harmonized_lower[i] === undefined ? null : v - (a.harmonized_lower[i] as number)));
  return {
    grid: { left: 64, right: 18, top: LEGEND_GRID_TOP, bottom: 30 },
    legend: { ...LEGEND_STYLE, data: ["Harmonized", "80 percent interval", "Raw MODIS", "Raw VIIRS S-NPP", "Raw VIIRS NOAA-20"] },
    tooltip: {
      ...TOOLTIP_STYLE,
      trigger: "axis",
      formatter: (params: unknown) => {
        const i = (params as { dataIndex: number }[])[0]?.dataIndex ?? 0;
        return [
          `<strong>${a.years[i]}</strong>`,
          `Harmonized: ${formatAmount(a.harmonized[i])} (${formatAmount(a.harmonized_lower[i])} to ${formatAmount(a.harmonized_upper[i])})`,
          `Raw MODIS: ${formatAmount(a.modis_raw[i])}`,
          `Raw VIIRS S-NPP: ${formatAmount(a.snpp_raw[i])}`,
          `Raw VIIRS NOAA-20: ${formatAmount(a.noaa20_raw[i])}`,
        ].join("<br/>");
      },
    },
    xAxis: { type: "category", data: a.years.map(String), ...AXIS_STYLE },
    yAxis: { type: "value", name: `${data.metric.unit_short} per year`, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, formatter: (v: number) => formatCompact(v) } },
    series: [
      { name: "lower", type: "line", data: a.harmonized_lower, stack: "band", symbol: "none", lineStyle: { opacity: 0 }, silent: true },
      { name: "80 percent interval", type: "line", data: band, stack: "band", symbol: "none", lineStyle: { opacity: 0 }, areaStyle: { color: "rgba(255,255,255,0.12)" }, itemStyle: { color: "rgba(255,255,255,0.3)" } },
      { name: "Raw MODIS", type: "line", data: a.modis_raw, symbolSize: 4, lineStyle: { color: PLATFORM_COLORS.AQUA, width: 1.5, type: "dashed" }, itemStyle: { color: PLATFORM_COLORS.AQUA } },
      { name: "Raw VIIRS S-NPP", type: "line", data: a.snpp_raw, symbolSize: 4, lineStyle: { color: PLATFORM_COLORS.SNPP, width: 1.5, type: "dashed" }, itemStyle: { color: PLATFORM_COLORS.SNPP } },
      { name: "Raw VIIRS NOAA-20", type: "line", data: a.noaa20_raw, symbolSize: 4, lineStyle: { color: PLATFORM_COLORS.NOAA20, width: 1.5, type: "dashed" }, itemStyle: { color: PLATFORM_COLORS.NOAA20 } },
      {
        name: "Harmonized",
        type: "line",
        data: a.harmonized,
        symbolSize: 5,
        lineStyle: { color: THEME.text, width: 2.6 },
        itemStyle: { color: THEME.text },
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: THEME.faint, type: "dotted" },
          label: { color: THEME.muted, fontSize: 10, formatter: "VIIRS S-NPP begins" },
          data: [{ xAxis: "2012" }],
        },
      },
    ],
  };
}

function scatterOption(set: PredictorSet, unit: string): ChartOption {
  const sc = set.scatter;
  if (!sc) return {};
  const points: [number, number, number, number][] = [];
  let hi = 1;
  for (let i = 0; i < sc.x.length; i += 1) {
    const x = sc.x[i];
    const y = sc.y[i];
    if (x === null || x === undefined || y === null || y === undefined) continue;
    points.push([x + 1, y + 1, sc.year[i] ?? 0, sc.week[i] ?? 0]);
    hi = Math.max(hi, x + 1, y + 1);
  }
  const top = Math.pow(10, Math.ceil(Math.log10(hi)));
  const ratio = set.candidates?.find((c) => c.name === "ratio")?.params.ratio;
  const lines: { name: string; data: [number, number][]; color: string }[] = [
    { name: "One to one (no correction)", data: [[1, 1], [top, top]], color: THEME.faint },
  ];
  if (ratio && ratio > 0) {
    lines.push({ name: `Single ratio (${ratio.toFixed(2)}x)`, data: [[1, 1], [top / ratio + 1, top]], color: THEME.fire });
  }
  return {
    grid: { left: 64, right: 18, top: LEGEND_GRID_TOP, bottom: 46 },
    legend: { ...LEGEND_STYLE },
    tooltip: {
      ...TOOLTIP_STYLE,
      trigger: "item",
      formatter: (params: unknown) => {
        const p = params as { seriesType: string; value: [number, number, number, number] };
        if (p.seriesType !== "scatter") return "";
        const [x, y, year, week] = p.value;
        return `<strong>${year}, week ${week + 1}</strong><br/>${set.label}: ${formatAmount(x - 1)}<br/>VIIRS S-NPP: ${formatAmount(y - 1)}`;
      },
    },
    xAxis: { type: "log", name: `${set.label}, ${unit} per 7 days (plus 1)`, nameLocation: "middle", nameGap: 28, min: 1, max: top, ...AXIS_STYLE },
    yAxis: { type: "log", name: "VIIRS S-NPP (plus 1)", min: 1, max: top, ...AXIS_STYLE },
    series: [
      {
        name: "Overlap weeks",
        type: "scatter",
        data: points,
        symbolSize: 5,
        itemStyle: { color: PLATFORM_COLORS.SNPP, opacity: 0.55 },
        large: true,
      },
      ...lines.map((line) => ({
        name: line.name,
        type: "line",
        data: line.data,
        symbol: "none",
        lineStyle: { color: line.color, width: 1.6, type: line.color === THEME.faint ? "dashed" : "solid" },
        itemStyle: { color: line.color },
        silent: true,
      })),
    ],
  };
}

function validationOption(set: PredictorSet, unit: string): ChartOption {
  const v = set.validation;
  if (!v) return {};
  const band = v.upper.map((u, i) => (u === null || v.lower[i] === null || v.lower[i] === undefined ? null : u - (v.lower[i] as number)));
  return {
    grid: { left: 64, right: 18, top: LEGEND_GRID_TOP, bottom: 62 },
    legend: { ...LEGEND_STYLE, data: ["Observed by VIIRS S-NPP", "Predicted on held out year", "80 percent interval"] },
    tooltip: {
      ...TOOLTIP_STYLE,
      trigger: "axis",
      formatter: (params: unknown) => {
        const i = (params as { dataIndex: number }[])[0]?.dataIndex ?? 0;
        return [
          `<strong>Week of ${formatDate(v.dates[i])}</strong>`,
          `Observed: ${formatAmount(v.observed[i])}`,
          `Predicted (held out): ${formatAmount(v.predicted[i])}`,
          `Interval: ${formatAmount(v.lower[i])} to ${formatAmount(v.upper[i])}`,
        ].join("<br/>");
      },
    },
    xAxis: { type: "category", data: v.dates, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, formatter: (d: string) => d.slice(0, 4), interval: 51 } },
    yAxis: { type: "value", name: `${unit} per 7 days`, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, formatter: (x: number) => formatCompact(x) } },
    dataZoom: [
      { type: "inside", start: 55, end: 100 },
      { type: "slider", height: 18, bottom: 8, start: 55, end: 100, showDetail: false, brushSelect: false, borderColor: THEME.border, fillerColor: "rgba(79,179,255,0.15)" },
    ],
    series: [
      { name: "lower", type: "line", data: v.lower, stack: "band", symbol: "none", lineStyle: { opacity: 0 }, silent: true },
      { name: "80 percent interval", type: "line", data: band, stack: "band", symbol: "none", lineStyle: { opacity: 0 }, areaStyle: { color: "rgba(255,138,61,0.22)" }, itemStyle: { color: "rgba(255,138,61,0.5)" } },
      { name: "Observed by VIIRS S-NPP", type: "line", data: v.observed, symbol: "none", lineStyle: { color: PLATFORM_COLORS.SNPP, width: 1.6 }, itemStyle: { color: PLATFORM_COLORS.SNPP } },
      { name: "Predicted on held out year", type: "line", data: v.predicted, symbol: "none", lineStyle: { color: THEME.fire, width: 1.6 }, itemStyle: { color: THEME.fire } },
    ],
  };
}

function factorOption(set: PredictorSet): ChartOption {
  const curve = set.factor_curve;
  const ticks = monthTicks(52);
  const labels = Array.from({ length: 52 }, (_, w) => ticks.find((t) => t.index === w)?.label ?? "");
  return {
    grid: { left: 52, right: 18, top: 18, bottom: 30 },
    tooltip: {
      ...TOOLTIP_STYLE,
      trigger: "axis",
      formatter: (params: unknown) => {
        const p = (params as { dataIndex: number; value: number | null }[])[0];
        return p ? `Week ${p.dataIndex + 1}: ${p.value === null ? "n/a" : `${p.value.toFixed(2)}x`}` : "";
      },
    },
    xAxis: { type: "category", data: labels, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, interval: 0 }, axisTick: { show: false } },
    yAxis: { type: "value", name: "S-NPP equivalent per predictor unit", ...AXIS_STYLE, scale: true },
    series: [
      { type: "line", data: curve?.factor ?? [], symbol: "none", lineStyle: { color: THEME.fire, width: 2 }, areaStyle: { color: "rgba(255,138,61,0.12)" } },
    ],
  };
}

function CoverageTimeline({ data }: { data: HarmonizationResponse }) {
  const start = parseIsoDate(data.record.start).getTime();
  const end = parseIsoDate(data.record.end).getTime() + 86_400_000;
  const span = end - start;
  const years: number[] = [];
  for (let y = 2002; y <= parseIsoDate(data.record.end).getUTCFullYear(); y += 4) years.push(y);
  const describe = (s: CoverageSegment) =>
    `${COVERAGE_LABELS[s.state]}: ${formatDate(s.start)} to ${formatDate(s.end)} (${formatNumber(s.days)} days)`;
  return (
    <div className="timeline" role="group" aria-label="Sensor coverage through the record">
      {data.coverage_timeline.map((row) => {
        const outages = row.segments.filter((s) => s.state === "outage");
        return (
          <div key={row.key} className="timeline-row">
            <div className="timeline-label">
              <span className="check-dot" style={{ background: PLATFORM_COLORS[row.key] }} aria-hidden="true" /> {row.label}
            </div>
            <div className="timeline-track" role="img" aria-label={`${row.label}: ${outages.length} outages detected, ${formatNumber(outages.reduce((a, s) => a + s.days, 0))} days`}>
              {row.segments
                .filter((s) => s.state !== "pre_launch")
                .map((s) => {
                  const left = ((parseIsoDate(s.start).getTime() - start) / span) * 100;
                  const width = ((s.days * 86_400_000) / span) * 100;
                  return (
                    <span
                      key={`${s.state}-${s.start}`}
                      className={`timeline-seg seg-${s.state}`}
                      style={{ left: `${left}%`, width: `${width}%`, background: COVERAGE_COLORS[s.state] }}
                      title={describe(s)}
                    />
                  );
                })}
            </div>
          </div>
        );
      })}
      <div className="timeline-row">
        <div className="timeline-label" />
        <div className="timeline-axis" aria-hidden="true">
          {years.map((y) => (
            <span key={y} style={{ left: `${((Date.UTC(y, 0, 1) - start) / span) * 100}%` }}>
              {y}
            </span>
          ))}
        </div>
      </div>
      <div className="legend-row">
        {(["observed", "outage", "provisional", "not_ingested"] as const).map((state) => (
          <Swatch key={state} color={COVERAGE_COLORS[state] ?? THEME.missing} label={COVERAGE_LABELS[state] ?? state} />
        ))}
      </div>
    </div>
  );
}

function ratioFeatures(grid: RatioGridResponse | undefined): { cells: MapFeatureCollection | null; low: number; high: number } {
  if (!grid || !grid.available) return { cells: null, low: 0, high: 1 };
  const low = grid.ratio_p10 ?? 1;
  const high = Math.max(grid.ratio_p90 ?? low + 1, low + 0.5);
  const features: MapFeatureCollection["features"] = [];
  for (let i = 0; i < grid.cells; i += 1) {
    const ratio = grid.ratio[i];
    if (ratio === null || ratio === undefined) continue;
    features.push({
      type: "Feature",
      geometry: { type: "Polygon", coordinates: cellPolygon(grid.west[i] ?? 0, grid.south[i] ?? 0, grid.step) },
      properties: { color: ratioColor(ratio, low, high), ratio, modis: grid.modis[i] ?? 0, viirs: grid.viirs[i] ?? 0 },
    });
  }
  return { cells: { type: "FeatureCollection", features }, low, high };
}

export function HarmonizationTab() {
  const { state } = useAppState();
  const aoi = useAoi();
  const query = useHarmonization();
  const grid = useRatioGrid(true);
  const [setKey, setSetKey] = useState<SetKey>("TA");

  const ratio = useMemo(() => ratioFeatures(grid.data), [grid.data]);
  const describeCell = useCallback((properties: Record<string, unknown>) => {
    const rows: PopupRow[] = [
      { label: "VIIRS S-NPP detections", value: formatNumber(Number(properties.viirs)) },
      { label: "MODIS detections", value: formatNumber(Number(properties.modis)) },
    ];
    return { title: `VIIRS to MODIS ratio ${Number(properties.ratio).toFixed(1)}x`, rows };
  }, []);

  return (
    <div className="tab-stack">
      <QueryState query={query} loadingLabel="Fitting and cross validating the calibration models" height={420}>
        {(data) => (
          <HarmonizationView
            data={data}
            setKey={setKey}
            onSetKey={setSetKey}
            grid={grid.data}
            ratio={ratio}
            aoiGeometry={aoi.geometry}
            aoiId={aoi.info?.id ?? null}
            aoiBounds={aoi.info?.bbox ?? null}
            basemap={state.basemap === "truecolor" ? "bluemarble" : state.basemap}
            describeCell={describeCell}
          />
        )}
      </QueryState>
    </div>
  );
}

interface ViewProps {
  data: HarmonizationResponse;
  setKey: SetKey;
  onSetKey: (key: SetKey) => void;
  grid: RatioGridResponse | undefined;
  ratio: { cells: MapFeatureCollection | null; low: number; high: number };
  aoiGeometry: AreaGeometry | null;
  aoiId: string | null;
  aoiBounds: [number, number, number, number] | null;
  basemap: Basemap;
  describeCell: (properties: Record<string, unknown>) => { title: string; rows: PopupRow[] };
}

function HarmonizationView({ data, setKey, onSetKey, grid, ratio, aoiGeometry, aoiId, aoiBounds, basemap, describeCell }: ViewProps) {
  const reference = useReferenceStates();
  const annual = useMemo(() => annualOption(data), [data]);
  const slug = fileSlug(data.aoi.name);
          const available = data.sets.filter((s) => s.available);
          const selected = data.sets.find((s) => s.key === setKey && s.available) ?? available[0];
          const main = data.sets.find((s) => s.key === "TA" && s.available);
          const best = main?.candidates?.find((c) => c.selected);
          const identity = main?.candidates?.find((c) => c.name === "identity");
          return (
            <>
              <Card
                title="One record instead of two"
                subtitle="Annual totals for the selected area. Dashed lines are the sensors as detected. The solid line is the harmonized record in VIIRS S-NPP equivalent units. The shaded band adds up weekly 80 percent intervals, which makes it a cautious outer bound for a whole year."
              >
                <div className="stat-grid">
                  <Stat
                    label="Step at 2012, sensors as detected"
                    value={formatRatio(data.continuity.naive_step)}
                    hint="mean of 2012 to 2014 over mean of 2009 to 2011, switching from MODIS to VIIRS"
                    tone="danger"
                  />
                  <Stat
                    label="Step at 2012, harmonized"
                    value={formatRatio(data.continuity.harmonized_step)}
                    hint={
                      data.continuity.modis_step === null
                        ? "same years, one unit. Any remaining change is real change in burning"
                        : `same years, one unit. MODIS on its own shows ${formatRatio(data.continuity.modis_step)} over these years, so what remains is real change in burning`
                    }
                    tone="ok"
                  />
                  <Stat
                    label="Error without correction"
                    value={formatPercent(identity?.wape ?? null)}
                    hint="weekly error on held out years when MODIS counts are used as they are"
                  />
                  <Stat
                    label="Error after harmonization"
                    value={formatPercent(best?.wape ?? null)}
                    hint={best ? `${best.label}, leave one year out` : "main model not available"}
                    tone="cool"
                  />
                </div>
                <EChart option={annual} height={320} ariaLabel="Annual totals by sensor and the harmonized record" downloadName={`agnisetu_annual_record_${slug}`} />
                <div className="legend-row">
                  <Swatch color={VALUE_TYPE_COLORS.modis_harmonized} label={`${formatNumber(data.days_by_type.modis_harmonized)} days estimated from MODIS`} />
                  <Swatch color={VALUE_TYPE_COLORS.reference} label={`${formatNumber(data.days_by_type.reference)} days observed by S-NPP`} />
                  <Swatch color={VALUE_TYPE_COLORS.viirs_calibrated} label={`${formatNumber(data.days_by_type.viirs_calibrated)} days from NOAA-20, calibrated`} />
                  <Swatch color={VALUE_TYPE_COLORS.missing} label={`${formatNumber(data.days_by_type.missing)} days with no sensor`} />
                </div>
              </Card>

              <Card
                title="Calibration models and their validation"
                subtitle="Each set of predictor sensors gets its own model. Candidates are compared on years they never saw during fitting; the simplest model within 2 percent of the best error is used."
              >
                {available.length === 0 || !selected ? (
                  <EmptyBlock title="No calibration could be fitted">
                    The sensors do not overlap enough in the ingested data.
                  </EmptyBlock>
                ) : (
                  <>
                    <div className="toolbar">
                      <Segmented<SetKey>
                        label="Predictor sensors"
                        value={selected.key}
                        onChange={onSetKey}
                        options={available.map((s) => ({ value: s.key, label: s.label }))}
                      />
                    </div>
                    <p className="small">
                      <strong>{selected.label}</strong> against VIIRS S-NPP: {formatNumber(selected.n_weeks ?? 0)} overlap weeks in{" "}
                      {selected.n_years} years ({formatDate(selected.overlap_start)} to {formatDate(selected.overlap_end)}),{" "}
                      {formatNumber(selected.predictor_detections ?? 0)} predictor detections. Used for{" "}
                      {formatNumber(selected.days_used ?? 0)} days of the harmonized record.
                    </p>
                    {selected.scope !== "area" && (
                      <Notice tone="warn">
                        This area has too few overlapping detections for a stable fit, so the model was trained on{" "}
                        {selected.scope_label}.
                      </Notice>
                    )}
                    <div className="table-scroll">
                      <table className="table">
                        <caption className="sr-only">Candidate calibration models with cross validated skill</caption>
                        <thead>
                          <tr>
                            <th scope="col">Model</th>
                            <th scope="col" className="num" title="Sum of absolute weekly errors divided by the observed total, on held out years">Weekly error</th>
                            <th scope="col" className="num" title="Predicted total divided by observed total, minus one">Bias</th>
                            <th scope="col" className="num" title="R squared on the log(1 + y) scale">R squared (log)</th>
                            <th scope="col" className="num" title="Mean absolute error of annual totals">Annual error</th>
                            <th scope="col">Used</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(selected.candidates ?? []).map((c) => (
                            <tr key={c.name} className={c.selected ? "is-selected" : ""}>
                              <th scope="row">{c.label}</th>
                              <td className="num">{formatPercent(c.wape, 1)}</td>
                              <td className="num">{formatSignedPercent(c.bias, 1)}</td>
                              <td className="num">{c.r2_log === null ? "n/a" : c.r2_log.toFixed(3)}</td>
                              <td className="num">{formatPercent(c.annual_mape, 1)}</td>
                              <td>{c.selected ? "Selected" : ""}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="chart-grid">
                      <div>
                        <h3 className="chart-title">Overlap weeks</h3>
                        <EChart option={scatterOption(selected, data.metric.unit_short)} height={320} ariaLabel={`Weekly ${selected.label} against VIIRS S-NPP on logarithmic axes`} downloadName={`agnisetu_overlap_weeks_${slug}_${selected.key}`} />
                      </div>
                      <div>
                        <h3 className="chart-title">Effective factor through the year</h3>
                        <EChart option={factorOption(selected)} height={320} ariaLabel="Calibration factor by week of year" downloadName={`agnisetu_calibration_factor_${slug}_${selected.key}`} />
                        <p className="muted small">
                          S-NPP equivalent produced per unit of the predictor at a typical active week
                          {selected.factor_curve?.typical_x ? ` (${formatAmount(selected.factor_curve.typical_x)} per 7 days)` : ""}.
                        </p>
                      </div>
                    </div>
                    <h3 className="chart-title">Held out predictions against observations</h3>
                    <EChart option={validationOption(selected, data.metric.unit_short)} height={300} ariaLabel="Observed VIIRS S-NPP weekly activity and predictions made without that year" downloadName={`agnisetu_validation_${slug}_${selected.key}`} />
                  </>
                )}
                {data.sets.filter((s) => !s.available).map((s) => (
                  <Notice key={s.key} tone="info">
                    {s.label} is not used: {s.reason}
                  </Notice>
                ))}
              </Card>

              <Card
                title="Where the sensors disagree most"
                subtitle={
                  grid?.available
                    ? `VIIRS S-NPP detections per MODIS detection, ${formatDate(grid.period.start)} to ${formatDate(grid.period.end)}, in ${grid.step} degree cells with at least ${grid.min_modis} MODIS detections.`
                    : "VIIRS S-NPP detections per MODIS detection over the overlap years."
                }
              >
                {grid?.available && (
                  <div className="stat-grid">
                    <Stat label="Area wide ratio" value={formatRatio(grid.overall_ratio)} />
                    <Stat label="Median cell" value={formatRatio(grid.ratio_median)} />
                    <Stat label="Range across cells" value={`${formatRatio(grid.ratio_p10)} to ${formatRatio(grid.ratio_p90)}`} hint="10th to 90th percentile" />
                    <Stat label="Cells with enough data" value={formatNumber(grid.cells_with_ratio)} hint={`of ${formatNumber(grid.cells)}`} />
                  </div>
                )}
                {grid && !grid.available && <Notice tone="info">{grid.reason}</Notice>}
                <MapView
                  basemap={basemap}
                  imageryDate={null}
                  aoi={aoiGeometry}
                  fitKey={aoiId}
                  fitBounds={aoiBounds}
                  cells={ratio.cells}
                  reference={reference.data ?? null}
                  describeFeature={describeCell}
                  ariaLabel="Map of the VIIRS to MODIS detection ratio"
                  height={440}
                />
                <div className="legend-row">
                  <RampLegend gradient={ratioRampCss()} low={`${ratio.low.toFixed(1)}x`} high={`${ratio.high.toFixed(1)}x or more`} label="VIIRS per MODIS detection" />
                </div>
                <p className="muted small">
                  A single national scaling factor would be wrong almost everywhere. Small agricultural fires push the
                  ratio up; large forest fires pull it down. This is why calibration is fitted per area and season.
                </p>
              </Card>

              <Card
                title="Sensor coverage and detected outages"
                subtitle="A silent sensor is a missing observation, not a day without fire. Outages are found from the data: days with zero detections across the whole domain when that sensor normally reports many on those calendar days."
              >
                <CoverageTimeline data={data} />
                <p className="muted small">
                  Source priority for each day: VIIRS S-NPP observed, then{" "}
                  {data.priority.filter((p) => p.available).map((p) => p.label).join(", then ")}. Days with no usable
                  sensor stay missing.
                </p>
              </Card>
            </>
          );
}
