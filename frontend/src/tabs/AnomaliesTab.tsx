import { useCallback, useMemo } from "react";
import { useAnomalies } from "../api/hooks";
import type { AnomaliesResponse, AnomalyClass, SeasonRow } from "../api/types";
import { AXIS_STYLE, EChart, LEGEND_GRID_TOP, LEGEND_STYLE, TOOLTIP_STYLE, type ChartOption } from "../components/EChart";
import { HeatGrid, type CellInfo } from "../components/HeatGrid";
import { Badge, Card, Notice, QueryState, Segmented, Spinner, Swatch } from "../components/ui";
import { ANOMALY_COLORS, ANOMALY_ORDER, THEME } from "../lib/colors";
import { fileSlug } from "../lib/download";
import { doyLabel, formatAmount, formatCompact, formatPercent, formatRatio, formatSignedPercent, monthTicks, ordinal, separatedLabels } from "../lib/format";
import { useAppState } from "../state/AppState";

function offsetLabel(offset: number): string {
  // Offsets count days from January 1 of the season year and may run past December.
  const wrapped = ((Math.round(offset) % 365) + 365) % 365;
  return doyLabel(wrapped + 1);
}

function classLabel(data: AnomaliesResponse, key: AnomalyClass | null | undefined): string {
  return data.classes.find((c) => c.key === key)?.label ?? "Not scored";
}

function totalsOption(data: AnomaliesResponse, selectedYear: number | null): ChartOption {
  const rows = data.seasons;
  const ref = data.reference;
  const top = Math.max(...rows.map((r) => r.upper ?? r.total ?? 0), ref?.p90 ?? 0);
  const lineLabels = separatedLabels([ref?.median, ref?.p90, ref?.p10], top);
  return {
    grid: { left: 64, right: 86, top: 20, bottom: 30 },
    tooltip: {
      ...TOOLTIP_STYLE,
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params: unknown) => {
        const i = (params as { dataIndex: number }[])[0]?.dataIndex ?? 0;
        const r = rows[i];
        if (!r) return "";
        if (r.total === null) return `<strong>${r.year}</strong><br/>Not enough coverage in this period`;
        const lines = [`<strong>${r.year}</strong>`, `Season total: ${formatAmount(r.total)}`];
        if (r.available) {
          lines.push(`${formatRatio(r.ratio_to_median)} the median of other years`);
          lines.push(`${ordinal(r.percentile ?? 0)} percentile, ${classLabel(data, r.class)}`);
          if ((r.estimated_share ?? 0) > 0.05) {
            lines.push(`${formatPercent(r.estimated_share)} from estimates; interval ${formatAmount(r.lower)} to ${formatAmount(r.upper)}`);
          }
          if (r.record_high) lines.push("Highest on record");
          if (r.record_low) lines.push("Lowest on record");
        }
        return lines.join("<br/>");
      },
    },
    xAxis: { type: "category", data: rows.map((r) => String(r.year)), ...AXIS_STYLE },
    yAxis: { type: "value", name: `${data.metric.unit_short} in the period`, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, formatter: (v: number) => formatCompact(v) } },
    series: [
      {
        type: "bar",
        barMaxWidth: 26,
        data: rows.map((r) => ({
          value: r.total,
          itemStyle: {
            color: r.class ? ANOMALY_COLORS[r.class] : THEME.faint,
            opacity: (r.estimated_share ?? 0) > 0.5 ? 0.62 : 1,
            borderColor: r.year === selectedYear ? "#ffffff" : "transparent",
            borderWidth: r.year === selectedYear ? 2 : 0,
          },
        })),
        markLine: ref
          ? {
              silent: true,
              symbol: "none",
              label: { color: THEME.muted, fontSize: 10, position: "end" },
              data: [
                { yAxis: ref.median ?? 0, name: "Median", lineStyle: { color: THEME.text, type: "solid", width: 1 }, label: { formatter: "Median", show: lineLabels[0] } },
                { yAxis: ref.p90 ?? 0, name: "p90", lineStyle: { color: ANOMALY_COLORS.far_above, type: "dashed" }, label: { formatter: "90th pct", show: lineLabels[1] } },
                { yAxis: ref.p10 ?? 0, name: "p10", lineStyle: { color: ANOMALY_COLORS.far_below, type: "dashed" }, label: { formatter: "10th pct", show: lineLabels[2] } },
              ],
            }
          : undefined,
      },
    ],
  };
}

function timingOption(data: AnomaliesResponse): ChartOption {
  const rows = data.seasons;
  const years = rows.map((r) => String(r.year));
  const pick = (key: "onset_offset" | "peak_offset" | "end_offset") => rows.map((r) => (r.available ? (r[key] ?? null) : null));
  const trend = data.trends?.onset;
  const trendLine =
    trend?.available && trend.slope_per_year !== null && trend.slope_per_year !== undefined && trend.intercept !== null && trend.intercept !== undefined
      ? rows.map((r) => (r.year >= (trend.first_year ?? 0) && r.year <= (trend.last_year ?? 9999) ? (trend.intercept as number) + (trend.slope_per_year as number) * r.year : null))
      : null;
  return {
    grid: { left: 64, right: 18, top: LEGEND_GRID_TOP, bottom: 30 },
    legend: { ...LEGEND_STYLE },
    tooltip: {
      ...TOOLTIP_STYLE,
      trigger: "axis",
      formatter: (params: unknown) => {
        const i = (params as { dataIndex: number }[])[0]?.dataIndex ?? 0;
        const r = rows[i];
        if (!r || !r.available || r.onset === null || r.onset === undefined) return `<strong>${r?.year ?? ""}</strong><br/>Timing not defined (weak or incomplete season)`;
        const shift = (v: number | null | undefined) => (v === null || v === undefined ? "" : ` (${v > 0 ? "+" : ""}${v} days against the median)`);
        return [
          `<strong>${r.year}</strong>`,
          `Onset: ${r.onset}${shift(r.onset_shift_days)}`,
          `Peak: ${r.peak ?? "n/a"}${shift(r.peak_shift_days)}`,
          `End: ${r.end ?? "n/a"}${shift(r.end_shift_days)}`,
        ].join("<br/>");
      },
    },
    xAxis: { type: "category", data: years, ...AXIS_STYLE },
    yAxis: { type: "value", scale: true, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, formatter: (v: number) => offsetLabel(v) } },
    series: [
      { name: "End (90 percent of season)", type: "line", data: pick("end_offset"), symbolSize: 5, connectNulls: false, lineStyle: { color: THEME.faint, width: 1.2 }, itemStyle: { color: THEME.muted } },
      { name: "Peak week", type: "line", data: pick("peak_offset"), symbolSize: 5, connectNulls: false, lineStyle: { color: THEME.fire, width: 1.4 }, itemStyle: { color: THEME.fire } },
      { name: "Onset (10 percent of season)", type: "line", data: pick("onset_offset"), symbolSize: 6, connectNulls: false, lineStyle: { color: THEME.cool, width: 1.8 }, itemStyle: { color: THEME.cool } },
      ...(trendLine
        ? [{ name: "Onset trend (Theil-Sen)", type: "line", data: trendLine, symbol: "none", connectNulls: true, lineStyle: { color: THEME.text, width: 1.4, type: "dashed" }, itemStyle: { color: THEME.text } }]
        : []),
    ],
  };
}

function shiftText(days: number | null | undefined): string {
  if (days === null || days === undefined) return "n/a";
  if (days === 0) return "on the median";
  return `${Math.abs(days)} days ${days > 0 ? "late" : "early"}`;
}

function RankingTable({ data, onSelect, selectedYear }: { data: AnomaliesResponse; onSelect: (year: number) => void; selectedYear: number | null }) {
  if (data.ranking.length === 0) {
    return <Notice tone="info">Not enough complete seasons to rank.</Notice>;
  }
  return (
    <div className="table-scroll">
      <table className="table">
        <caption className="sr-only">Most unusual seasons by robust z score</caption>
        <thead>
          <tr>
            <th scope="col">Year</th>
            <th scope="col">Class</th>
            <th scope="col" className="num">Season total</th>
            <th scope="col" className="num">Against median</th>
            <th scope="col" className="num">Against average</th>
            <th scope="col" className="num">Percentile</th>
            <th scope="col" className="num">Robust z</th>
            <th scope="col">Onset</th>
            <th scope="col">Notes</th>
          </tr>
        </thead>
        <tbody>
          {data.ranking.map((r: SeasonRow) => (
            <tr key={r.year} className={r.year === selectedYear ? "is-selected" : ""}>
              <th scope="row">
                <button type="button" className="link-button" onClick={() => onSelect(r.year)} aria-pressed={r.year === selectedYear}>
                  {r.year}
                </button>
              </th>
              <td>{r.class ? <Badge color={ANOMALY_COLORS[r.class]}>{classLabel(data, r.class)}</Badge> : "n/a"}</td>
              <td className="num">{formatAmount(r.total)}</td>
              <td className="num">{formatRatio(r.ratio_to_median)}</td>
              <td className="num">{formatSignedPercent(r.anomaly_vs_mean)}</td>
              <td className="num">{r.percentile === null || r.percentile === undefined ? "n/a" : ordinal(r.percentile)}</td>
              <td className="num">{r.robust_z === null || r.robust_z === undefined ? "n/a" : r.robust_z.toFixed(2)}</td>
              <td>{r.onset ? `${r.onset}, ${shiftText(r.onset_shift_days)}` : "n/a"}</td>
              <td>
                {[
                  r.record_high ? "Record high" : null,
                  r.record_low ? "Record low" : null,
                  (r.estimated_share ?? 0) > 0.5 ? "Mostly estimated from MODIS" : null,
                ]
                  .filter(Boolean)
                  .join(". ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AnomaliesTab() {
  const { state, update } = useAppState();
  const query = useAnomalies();
  const data = query.data;
  const ticks = useMemo(() => monthTicks(52), []);

  const cellColor = useCallback(
    (row: number, column: number) => {
      const index = data?.weekly.class_index[row]?.[column];
      if (index === undefined || index < 0) return null;
      const key = ANOMALY_ORDER[index];
      return key ? ANOMALY_COLORS[key] : null;
    },
    [data],
  );
  const cellInfo = useCallback(
    (row: number, column: number): CellInfo | null => {
      if (!data) return null;
      const title = `${data.years[row]}, ${data.weeks[column]?.label ?? ""}`;
      const index = data.weekly.class_index[row]?.[column];
      const percentile = data.weekly.percentile[row]?.[column];
      if (index === undefined || index < 0 || percentile === null || percentile === undefined) {
        return { title, lines: ["Not scored: missing data or too few comparison years."] };
      }
      return {
        title,
        lines: [classLabel(data, ANOMALY_ORDER[index]), `${ordinal(percentile)} percentile against the same week in other years`],
      };
    },
    [data],
  );
  const totals = useMemo(() => (data ? totalsOption(data, state.year) : null), [data, state.year]);
  const timing = useMemo(() => (data ? timingOption(data) : null), [data]);

  return (
    <div className="tab-stack">
      <QueryState query={query} loadingLabel="Scoring every season against the others" height={420}>
        {(d) => {
          const period = d.periods.find((p) => p.id === d.selected_period);
          const selectedRow = state.year !== null ? d.years.indexOf(state.year) : -1;
          return (
            <>
              <Card
                title="Unusual seasons"
                subtitle={
                  period
                    ? `${period.name} (${period.window_label}). Each year is compared with every other complete year from ${d.baseline.start} to ${d.baseline.end}, leaving itself out.`
                    : "No critical period could be defined for this area."
                }
                actions={query.isFetching ? <Spinner label="Updating" /> : undefined}
              >
                {d.periods.length > 1 && (
                  <div className="toolbar">
                    <Segmented
                      label="Critical period"
                      value={d.selected_period ?? ""}
                      onChange={(periodId) => update({ periodId })}
                      options={d.periods.map((p) => ({ value: p.id, label: p.name, title: p.window_label }))}
                    />
                  </div>
                )}
                {!period || !totals ? (
                  <Notice tone="info">This area has too little recorded burning to score seasons.</Notice>
                ) : (
                  <>
                    <EChart
                      option={totals}
                      height={320}
                      ariaLabel={`Season totals by year for the ${period.name}, colored by anomaly class`}
                      onClick={(p) => update({ year: Number(p.name) })}
                      downloadName={`agnisetu_season_totals_${fileSlug(d.aoi.name)}_${fileSlug(period.name)}`}
                    />
                    <div className="legend-row">
                      {ANOMALY_ORDER.map((key) => (
                        <Swatch key={key} color={ANOMALY_COLORS[key]} label={classLabel(d, key)} />
                      ))}
                      <span className="muted small">Paler bars are mostly harmonized estimates. Solid line: median of the baseline years. Dashed lines: 10th and 90th percentile.</span>
                    </div>
                    <h3 className="chart-title">Most unusual seasons</h3>
                    <RankingTable data={d} onSelect={(year) => update({ year })} selectedYear={state.year} />
                  </>
                )}
              </Card>

              {period && d.trends && (
                <Card title="Long term change" subtitle="Theil-Sen slope with the Mann-Kendall test. Both are robust to single extreme years.">
                  <ul className="finding-list">
                    <li>
                      <span className={`finding-flag ${d.trends.total.significant ? "is-strong" : ""}`}>{d.trends.total.significant ? "Significant" : "Not significant"}</span>
                      {d.trends.total.text}
                    </li>
                    <li>
                      <span className={`finding-flag ${d.trends.onset.significant ? "is-strong" : ""}`}>{d.trends.onset.significant ? "Significant" : "Not significant"}</span>
                      {d.trends.onset.text}
                    </li>
                    <li>
                      <span className={`finding-flag ${d.trends.peak.significant ? "is-strong" : ""}`}>{d.trends.peak.significant ? "Significant" : "Not significant"}</span>
                      {d.trends.peak.text}
                    </li>
                  </ul>
                  {timing && (
                    <>
                      <h3 className="chart-title">Season timing by year</h3>
                      <EChart option={timing} height={300} ariaLabel="Onset, peak and end date of the season in each year" downloadName={`agnisetu_season_timing_${fileSlug(d.aoi.name)}_${fileSlug(period.name)}`} />
                      <p className="muted small">
                        Years with a season total under 20 percent of the median are left out because their timing is not
                        stable.
                      </p>
                    </>
                  )}
                </Card>
              )}

              <Card
                title="Week by week anomalies"
                subtitle="Each cell is the percentile class of that week against the same week in other years. Off season weeks with no burning in any year are near normal by definition."
              >
                <HeatGrid
                  rows={d.years.map(String)}
                  columns={52}
                  columnTicks={ticks}
                  cellColor={cellColor}
                  cellInfo={cellInfo}
                  selectedRow={selectedRow >= 0 ? selectedRow : null}
                  onSelectRow={(row) => update({ year: d.years[row] ?? null })}
                  bands={d.periods.map((p) => ({ start: p.start_week, end: p.end_week, label: p.name }))}
                  ariaLabel={`Weekly anomaly class by year for ${d.aoi.name}`}
                />
                <div className="legend-row">
                  {ANOMALY_ORDER.map((key) => (
                    <Swatch key={key} color={ANOMALY_COLORS[key]} label={classLabel(d, key)} />
                  ))}
                  <Swatch color={THEME.missing} label="Not scored" hatched />
                </div>
              </Card>
            </>
          );
        }}
      </QueryState>
    </div>
  );
}
