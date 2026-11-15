import { useCallback, useMemo } from "react";
import { useCalendar } from "../api/hooks";
import type { CalendarResponse, Resolution, SeriesKind } from "../api/types";
import { AXIS_STYLE, EChart, LEGEND_GRID_TOP, LEGEND_STYLE, TOOLTIP_STYLE, type ChartOption } from "../components/EChart";
import { HeatGrid, type CellInfo, type ColumnBand } from "../components/HeatGrid";
import { Card, ErrorBlock, LoadingBlock, Notice, RampLegend, Segmented, Spinner, Swatch } from "../components/ui";
import { RAMP_ORDER, RAMPS, rampColor, rampCss, THEME, VALUE_TYPE_BY_CODE, VALUE_TYPE_COLORS, VALUE_TYPE_LABELS, type RampKey } from "../lib/colors";
import { fileSlug } from "../lib/download";
import { doyLabel, formatAmount, formatCompact, formatPercent, formatRatio, monthTicks, ordinal } from "../lib/format";
import { useAppState } from "../state/AppState";

const SERIES_OPTIONS: { value: SeriesKind; label: string; title: string }[] = [
  { value: "harmonized", label: "Harmonized", title: "One continuous record in VIIRS S-NPP equivalent units" },
  { value: "modis", label: "Raw MODIS", title: "Terra plus Aqua as detected, 1 km" },
  { value: "snpp", label: "Raw VIIRS S-NPP", title: "VIIRS S-NPP as detected, 375 m" },
  { value: "noaa20", label: "Raw VIIRS NOAA-20", title: "VIIRS NOAA-20 as detected, 375 m" },
];

const SERIES_NOTE: Record<SeriesKind, string> = {
  harmonized:
    "Every year is in the same unit. Years before 2012 are estimates from MODIS; 2012 onward is observed by VIIRS S-NPP, with NOAA-20 filling gaps.",
  modis: "MODIS as detected. The color scale is the same as the harmonized view, so the lower counts of the 1 km sensor are visible.",
  snpp: "VIIRS S-NPP as detected. The record starts in January 2012; earlier years have no data.",
  noaa20: "VIIRS NOAA-20 as detected. The record starts in April 2018; earlier years have no data.",
};

const MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTH_LONG = [
  "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
  "December",
];
const COLUMNS: Record<Resolution, number> = { weekly: 52, monthly: 12, daily: 366 };
const UNIT_SPAN: Record<Resolution, string> = { weekly: "per 7 days", monthly: "in the month", daily: "that day" };
const CELL_NAME: Record<Resolution, string> = { weekly: "week", monthly: "month", daily: "day" };

/** Month index (0 to 11) that a weekly bin starts in, in a non leap year. */
function monthOfWeek(week: number): number {
  const date = new Date(Date.UTC(2023, 0, 1 + 7 * week));
  return date.getUTCMonth();
}

function percentileAmong(value: number, column: (number | null)[]): number | null {
  const finite = column.filter((v): v is number => v !== null);
  if (finite.length < 5) return null;
  const below = finite.filter((v) => v < value).length;
  const equal = finite.filter((v) => v === value).length;
  return (100 * (below + 0.5 * equal)) / finite.length;
}

function dominantType(row: number[] | undefined): number {
  if (!row) return 0;
  const counts = [0, 0, 0, 0];
  for (const code of row) if (code > 0 && code < 4) counts[code] = (counts[code] ?? 0) + 1;
  let best = 0;
  for (let i = 1; i < 4; i += 1) if ((counts[i] ?? 0) > (counts[best] ?? 0)) best = i;
  return (counts[best] ?? 0) > 0 ? best : 0;
}

function climatologyOption(data: CalendarResponse, selectedYear: number | null): ChartOption {
  const c = data.climatology;
  const labels = data.weeks.map((w) => w.label);
  const diff = (hi: (number | null)[], lo: (number | null)[]) => hi.map((v, i) => (v === null || lo[i] === null || lo[i] === undefined ? null : v - (lo[i] as number)));
  const yearIndex = selectedYear === null ? -1 : data.years.indexOf(selectedYear);
  const yearRow = yearIndex >= 0 && data.resolution === "weekly" ? data.matrix[yearIndex] : undefined;
  // Period names are shortened and alternate between two rows so neighbors never run into each other.
  const areas = data.critical_periods.flatMap((p, index) => {
    const spans = p.start_week <= p.end_week ? [[p.start_week, p.end_week]] : [[p.start_week, 51], [0, p.end_week]];
    const name = p.name.replace(/ burning period$/, "");
    return spans.map(([from, to]) => [{ xAxis: labels[from ?? 0], name, label: { offset: [0, index % 2 === 0 ? 0 : 13] } }, { xAxis: labels[to ?? 0] }]);
  });
  return {
    grid: { left: 62, right: 18, top: LEGEND_GRID_TOP, bottom: 44 },
    legend: { ...LEGEND_STYLE, data: ["Median", "25 to 75 percent", "10 to 90 percent", ...(yearRow ? [String(selectedYear)] : [])] },
    tooltip: {
      ...TOOLTIP_STYLE,
      trigger: "axis",
      formatter: (params: unknown) => {
        const list = params as { dataIndex: number }[];
        const i = list[0]?.dataIndex ?? 0;
        const lines = [
          `<strong>${labels[i]}</strong>`,
          `Median: ${formatAmount(c.median[i])}`,
          `25 to 75 percent: ${formatAmount(c.p25[i])} to ${formatAmount(c.p75[i])}`,
          `10 to 90 percent: ${formatAmount(c.p10[i])} to ${formatAmount(c.p90[i])}`,
          `Highest year: ${formatAmount(c.max[i])}`,
        ];
        if (yearRow) lines.push(`${selectedYear}: ${formatAmount(yearRow[i])}`);
        return lines.join("<br/>");
      },
    },
    xAxis: { type: "category", data: labels, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, interval: 3, formatter: (v: string) => v.split(" to ")[0] } },
    yAxis: { type: "value", name: `${data.metric.unit_short} per 7 days`, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, formatter: (v: number) => formatCompact(v) } },
    series: [
      { name: "p10", type: "line", data: c.p10, stack: "outer", symbol: "none", lineStyle: { opacity: 0 }, silent: true, tooltip: { show: false } },
      { name: "10 to 90 percent", type: "line", data: diff(c.p90, c.p10), stack: "outer", symbol: "none", lineStyle: { opacity: 0 }, areaStyle: { color: "rgba(255, 138, 61, 0.16)" }, itemStyle: { color: "rgba(255, 138, 61, 0.35)" } },
      { name: "p25", type: "line", data: c.p25, stack: "inner", symbol: "none", lineStyle: { opacity: 0 }, silent: true, tooltip: { show: false } },
      { name: "25 to 75 percent", type: "line", data: diff(c.p75, c.p25), stack: "inner", symbol: "none", lineStyle: { opacity: 0 }, areaStyle: { color: "rgba(255, 138, 61, 0.30)" }, itemStyle: { color: "rgba(255, 138, 61, 0.6)" } },
      {
        name: "Median",
        type: "line",
        data: c.median,
        symbol: "none",
        lineStyle: { color: THEME.fire, width: 2.2 },
        itemStyle: { color: THEME.fire },
        markArea: { silent: true, itemStyle: { color: "rgba(255,255,255,0.05)" }, label: { color: THEME.muted, fontSize: 10, position: "insideTop" }, data: areas },
      },
      ...(yearRow
        ? [{ name: String(selectedYear), type: "line", data: yearRow, symbol: "none", connectNulls: false, lineStyle: { color: THEME.text, width: 1.8 }, itemStyle: { color: THEME.text } }]
        : []),
    ],
  };
}

function monthlyOption(data: CalendarResponse, year: number): ChartOption {
  const clim = data.monthly_climatology;
  const yearIndex = data.years.indexOf(year);
  const row = yearIndex >= 0 ? (data.matrix[yearIndex] ?? []) : [];
  const median = clim?.median ?? [];
  const p90 = clim?.p90 ?? [];
  return {
    grid: { left: 62, right: 18, top: LEGEND_GRID_TOP, bottom: 28 },
    legend: { ...LEGEND_STYLE },
    tooltip: {
      ...TOOLTIP_STYLE,
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params: unknown) => {
        const i = (params as { dataIndex: number }[])[0]?.dataIndex ?? 0;
        const value = row[i];
        const typical = median[i];
        const lines = [
          `<strong>${MONTH_LONG[i]}</strong>`,
          `Historical median: ${formatAmount(typical)}`,
          `90th percentile: ${formatAmount(p90[i])}`,
          `Highest year: ${formatAmount(clim?.max[i])}`,
          value === null || value === undefined ? `${year}: not complete or no data` : `${year}: ${formatAmount(value)}`,
        ];
        if (value !== null && value !== undefined && typical) lines.push(`${formatRatio(value / typical)} the median`);
        return lines.join("<br/>");
      },
    },
    xAxis: { type: "category", data: MONTH_SHORT, ...AXIS_STYLE },
    yAxis: { type: "value", name: `${data.metric.unit_short} in the month`, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, formatter: (v: number) => formatCompact(v) } },
    series: [
      { name: "Historical median", type: "bar", data: median, barMaxWidth: 34, itemStyle: { color: "#3b4756" } },
      { name: String(year), type: "bar", data: row, barMaxWidth: 34, itemStyle: { color: THEME.fire } },
      { name: "90th percentile", type: "line", data: p90, symbol: "circle", symbolSize: 5, lineStyle: { color: THEME.text, width: 1.2, type: "dashed" }, itemStyle: { color: THEME.text } },
    ],
  };
}

function MonthsCard({ selectedYear }: { selectedYear: number | null }) {
  const monthly = useCalendar("harmonized", "monthly");
  const data = monthly.data;
  // Default to the latest year that has at least one complete month.
  const fallbackYear = useMemo(() => {
    if (!data) return null;
    for (let i = data.years.length - 1; i >= 0; i -= 1) {
      if ((data.matrix[i] ?? []).some((v) => v !== null)) return data.years[i] ?? null;
    }
    return null;
  }, [data]);
  const year = selectedYear ?? fallbackYear;
  const option = useMemo(() => (data && data.resolution === "monthly" && year !== null ? monthlyOption(data, year) : null), [data, year]);

  return (
    <Card
      title="Months and years with the most burning"
      subtitle="Calendar month totals of the harmonized record. A month is reported when at least 80 percent of its days have data, so a month still in progress is left out here; the Early Warning tab compares the current month so far."
    >
      {monthly.isError && data === undefined ? (
        <ErrorBlock error={monthly.error} onRetry={() => void monthly.refetch()} />
      ) : !data || data.resolution !== "monthly" || !option ? (
        <LoadingBlock label="Adding up calendar months" height={260} />
      ) : (
        <>
          <h3 className="chart-title">
            {year} against the historical median, month by month
          </h3>
          <EChart
            option={option}
            height={280}
            ariaLabel={`Monthly burning activity in ${year} against the historical median for ${data.aoi.name}`}
            downloadName={`agnisetu_months_${fileSlug(data.aoi.name)}_${year}`}
          />
          <div className="table-pair">
            <div>
              <h3 className="chart-title">Ten highest months on record</h3>
              <div className="table-scroll">
                <table className="table">
                  <caption className="sr-only">The ten calendar months with the most burning activity</caption>
                  <thead>
                    <tr>
                      <th scope="col" className="num">Rank</th>
                      <th scope="col">Month</th>
                      <th scope="col" className="num">Activity</th>
                      <th scope="col" className="num">Against that month's median</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.top_months ?? []).map((m, i) => (
                      <tr key={m.label}>
                        <td className="num">{i + 1}</td>
                        <th scope="row">{m.label}</th>
                        <td className="num">{formatAmount(m.value)}</td>
                        <td className="num">{formatRatio(m.ratio_to_median)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div>
              <h3 className="chart-title">Five highest years</h3>
              <div className="table-scroll">
                <table className="table">
                  <caption className="sr-only">The five years with the most burning activity</caption>
                  <thead>
                    <tr>
                      <th scope="col" className="num">Rank</th>
                      <th scope="col">Year</th>
                      <th scope="col" className="num">Activity</th>
                      <th scope="col" className="num">Against the median year</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.top_years ?? []).map((y, i) => (
                      <tr key={y.year}>
                        <td className="num">{i + 1}</td>
                        <th scope="row">{y.year}</th>
                        <td className="num">{formatAmount(y.value)}</td>
                        <td className="num">{formatRatio(y.ratio_to_median)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </>
      )}
    </Card>
  );
}

export function CalendarTab() {
  const { state, update } = useAppState();
  const view = useCalendar(state.series, state.resolution);
  // The harmonized response at the same resolution supplies the shared color scale.
  const scaleSource = useCalendar("harmonized", state.resolution);
  // The harmonized weekly response supplies the climatology and the critical periods.
  const reference = useCalendar("harmonized", "weekly");

  const data = view.data;
  const scaleMax = scaleSource.data?.scale_max ?? data?.scale_max ?? 1;
  const columns = COLUMNS[state.resolution];
  const ticks = useMemo(
    () => (columns === 12 ? MONTH_SHORT.map((label, index) => ({ index, label })) : monthTicks(columns)),
    [columns],
  );
  const matrixMatches = data !== undefined && data.series === state.series && data.resolution === state.resolution;

  const cellColor = useCallback(
    (row: number, column: number) => {
      const value = data?.matrix[row]?.[column];
      if (value === null || value === undefined) return null;
      return rampColor(state.ramp, value, scaleMax ?? 1);
    },
    [data, scaleMax, state.ramp],
  );

  const cellInfo = useCallback(
    (row: number, column: number): CellInfo | null => {
      if (!data) return null;
      const year = data.years[row];
      const value = data.matrix[row]?.[column];
      const when =
        data.resolution === "weekly"
          ? (data.weeks[column]?.label ?? "")
          : data.resolution === "monthly"
            ? (MONTH_LONG[column] ?? "")
            : doyLabel(column + 1);
      const title = `${year}, ${when}`;
      if (value === null || value === undefined) {
        const reason =
          data.resolution === "monthly"
            ? "No value: fewer than 80 percent of the days in this month have data. It may be before the sensor record, not ingested, or still in progress."
            : "No data: no sensor in this series was observing, or the days were not ingested.";
        return { title, lines: [reason] };
      }
      const lines = [`${formatAmount(value)} ${data.metric.unit_short} ${UNIT_SPAN[data.resolution]}`];
      const code = data.vtype?.[row]?.[column];
      if (code !== undefined) {
        const type = VALUE_TYPE_BY_CODE[code] ?? "missing";
        lines.push(VALUE_TYPE_LABELS[type]);
        const lower = data.lower?.[row]?.[column];
        const upper = data.upper?.[row]?.[column];
        if (type !== "reference" && lower !== null && lower !== undefined && upper !== null && upper !== undefined) {
          lines.push(`80 percent interval: ${formatAmount(lower)} to ${formatAmount(upper)}`);
        }
      }
      const p = percentileAmong(value, data.matrix.map((r) => r[column] ?? null));
      if (p !== null) lines.push(`${ordinal(p)} percentile among all years for this ${CELL_NAME[data.resolution]}`);
      if (data.resolution === "monthly") {
        const typical = data.monthly_climatology?.median[column];
        if (typical) lines.push(`${formatRatio(value / typical)} the historical median for ${MONTH_LONG[column]}`);
      }
      const covered = data.days_covered?.[row]?.[column];
      if (covered !== undefined) lines.push(`${covered} days with coverage`);
      return { title, lines };
    },
    [data],
  );

  const rowAccent = useCallback(
    (row: number) => {
      if (!data?.vtype) return null;
      const type = VALUE_TYPE_BY_CODE[dominantType(data.vtype[row])] ?? "missing";
      return type === "missing" ? null : VALUE_TYPE_COLORS[type];
    },
    [data],
  );

  const bands = useMemo<ColumnBand[]>(() => {
    const periods = reference.data?.critical_periods ?? [];
    return periods.map((p) => {
      if (state.resolution === "weekly") return { start: p.start_week, end: p.end_week, label: p.name };
      if (state.resolution === "monthly") return { start: monthOfWeek(p.start_week), end: monthOfWeek(p.end_week), label: p.name };
      return { start: p.start_week * 7, end: Math.min(365, p.end_week * 7 + 6), label: p.name };
    });
  }, [reference.data, state.resolution]);

  const climatology = useMemo(
    () => (reference.data ? climatologyOption(reference.data, state.year) : null),
    [reference.data, state.year],
  );

  const years = reference.data?.years ?? data?.years ?? [];
  const baseline = reference.data?.baseline;
  const selectedRow = state.year !== null && data ? data.years.indexOf(state.year) : -1;
  const areaSlug = fileSlug(data?.aoi.name ?? "area");

  const setBaseline = (start: number, end: number) => {
    if (end - start < 4) return;
    update({ baseline: { start, end } });
  };

  return (
    <div className="tab-stack">
      <Card
        title="Burning activity calendar"
        subtitle={SERIES_NOTE[state.series]}
        actions={view.isFetching ? <Spinner label="Updating the calendar" /> : undefined}
      >
        <div className="toolbar">
          <Segmented<SeriesKind> label="Series" value={state.series} onChange={(series) => update({ series })} options={SERIES_OPTIONS} />
          <Segmented<Resolution>
            label="Resolution"
            value={state.resolution}
            onChange={(resolution) => update({ resolution })}
            options={[
              { value: "monthly", label: "Monthly", title: "One column per calendar month" },
              { value: "weekly", label: "Weekly", title: "52 bins per year" },
              { value: "daily", label: "Daily", title: "One column per day of year" },
            ]}
          />
          <Segmented<RampKey>
            label="Color ramp"
            value={state.ramp}
            onChange={(ramp) => update({ ramp })}
            options={RAMP_ORDER.map((key) => ({ value: key, label: RAMPS[key].label, title: RAMPS[key].note }))}
          />
          {state.year !== null && (
            <button type="button" className="btn" onClick={() => update({ year: null })}>
              Clear year {state.year}
            </button>
          )}
        </div>
        {view.isError && data === undefined ? (
          <ErrorBlock error={view.error} onRetry={() => void view.refetch()} />
        ) : !data || !matrixMatches ? (
          <LoadingBlock label="Building the calendar" height={520} />
        ) : (
          <>
            <HeatGrid
              rows={data.years.map(String)}
              columns={columns}
              columnTicks={ticks}
              cellColor={cellColor}
              cellInfo={cellInfo}
              rowAccent={state.series === "harmonized" ? rowAccent : undefined}
              selectedRow={selectedRow >= 0 ? selectedRow : null}
              onSelectRow={(row) => update({ year: data.years[row] ?? null })}
              bands={bands}
              ariaLabel={`Burning activity by year and ${CELL_NAME[state.resolution]} for ${data.aoi.name}`}
              rowHeight={state.resolution === "daily" ? 17 : state.resolution === "monthly" ? 21 : 19}
              downloadName={`agnisetu_calendar_${areaSlug}_${state.series}_${state.resolution}`}
            />
            <div className="legend-row">
              <RampLegend
                gradient={rampCss(state.ramp)}
                low="0"
                high={`${formatCompact(scaleMax)} ${data.metric.unit_short} ${UNIT_SPAN[state.resolution]}`}
                label="Activity (square root scale)"
              />
              <Swatch color={THEME.missing} label="No data" hatched />
              <Swatch color={THEME.fire} label="Critical period (bar above the grid)" />
            </div>
            {state.series === "harmonized" && (
              <div className="legend-row">
                <span className="muted small">Bar beside each year:</span>
                <Swatch color={VALUE_TYPE_COLORS.modis_harmonized} label="mostly harmonized estimate from MODIS" />
                <Swatch color={VALUE_TYPE_COLORS.reference} label="mostly observed by VIIRS S-NPP" />
                <Swatch color={VALUE_TYPE_COLORS.viirs_calibrated} label="mostly NOAA-20 calibrated" />
              </div>
            )}
            <p className="muted small">
              Click a year to overlay it on the charts below and to carry it to the Anomalies tab. Use the arrow keys to
              read cells with the keyboard.
            </p>
          </>
        )}
      </Card>

      <MonthsCard selectedYear={state.year} />

      <Card
        title="Typical year"
        subtitle={
          baseline
            ? `Median and percentile bands of harmonized weekly activity over ${baseline.years_used.length} complete years (${baseline.start} to ${baseline.end}). Shaded spans are critical periods.`
            : undefined
        }
      >
        {reference.isError && reference.data === undefined ? (
          <ErrorBlock error={reference.error} onRetry={() => void reference.refetch()} />
        ) : !reference.data || !climatology ? (
          <LoadingBlock label="Computing the climatology" height={300} />
        ) : (
          <>
            <div className="toolbar">
              <label className="select-field">
                <span className="control-label">Baseline from</span>
                <select
                  value={baseline?.start ?? ""}
                  onChange={(event) => baseline && setBaseline(Number(event.target.value), Math.max(baseline.end, Number(event.target.value) + 4))}
                >
                  {years.filter((y) => !baseline || y <= baseline.end - 4 || y === baseline.start).map((y) => (
                    <option key={y} value={y}>
                      {y}
                    </option>
                  ))}
                </select>
              </label>
              <label className="select-field">
                <span className="control-label">Baseline to</span>
                <select
                  value={baseline?.end ?? ""}
                  onChange={(event) => baseline && setBaseline(Math.min(baseline.start, Number(event.target.value) - 4), Number(event.target.value))}
                >
                  {years.filter((y) => !baseline || y >= baseline.start + 4 || y === baseline.end).map((y) => (
                    <option key={y} value={y}>
                      {y}
                    </option>
                  ))}
                </select>
              </label>
              {state.baseline && (
                <button type="button" className="btn" onClick={() => update({ baseline: null })}>
                  Use all complete years
                </button>
              )}
            </div>
            <EChart
              option={climatology}
              height={320}
              ariaLabel="Median weekly burning activity through the year with percentile bands"
              downloadName={`agnisetu_typical_year_${areaSlug}`}
            />
          </>
        )}
      </Card>

      <Card
        title="Critical periods"
        subtitle="The smallest set of weeks that holds 80 percent of a typical year's activity, grouped into continuous periods. Timing statistics come from the day each year reached 10 and 90 percent of its season total."
      >
        {!reference.data ? (
          <LoadingBlock label="Detecting critical periods" height={120} />
        ) : reference.data.critical_periods.length === 0 ? (
          <Notice tone="info">This area has too little recorded burning to define a critical period.</Notice>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <caption className="sr-only">Critical burning periods for the selected area</caption>
              <thead>
                <tr>
                  <th scope="col">Period</th>
                  <th scope="col">Window</th>
                  <th scope="col" className="num">Share of year</th>
                  <th scope="col">Typical onset (middle half of years)</th>
                  <th scope="col">Earliest onset</th>
                  <th scope="col">Typical peak</th>
                  <th scope="col">Typical end</th>
                  <th scope="col" className="num">Length, days</th>
                  <th scope="col" className="num">Median total</th>
                </tr>
              </thead>
              <tbody>
                {reference.data.critical_periods.map((p) => (
                  <tr key={p.id}>
                    <th scope="row">{p.name}</th>
                    <td>{p.window_label}</td>
                    <td className="num">{formatPercent(p.share_of_annual)}</td>
                    <td>
                      {p.onset.median ?? "n/a"}
                      {p.onset.p25 && p.onset.p75 ? ` (${p.onset.p25} to ${p.onset.p75})` : ""}
                    </td>
                    <td>{p.onset.earliest ?? "n/a"}</td>
                    <td>{p.peak.median ?? "n/a"}</td>
                    <td>{p.end.median ?? "n/a"}</td>
                    <td className="num">{p.median_duration_days ?? "n/a"}</td>
                    <td className="num">{formatCompact(p.median_total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
