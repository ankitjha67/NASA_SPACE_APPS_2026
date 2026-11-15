import { useMemo, useState } from "react";
import { useWarning } from "../api/hooks";
import type { OutlookItem, RecentWindow, SeasonProgress, WarningResponse } from "../api/types";
import { AXIS_STYLE, EChart, LEGEND_GRID_TOP, LEGEND_STYLE, TOOLTIP_STYLE, type ChartOption } from "../components/EChart";
import { Card, Notice, QueryState, Spinner, Stat, Swatch } from "../components/ui";
import { LEVEL_COLORS, THEME, VALUE_TYPE_BY_CODE, VALUE_TYPE_COLORS, VALUE_TYPE_SHORT } from "../lib/colors";
import { fileSlug } from "../lib/download";
import { addDays, clampDate, formatAmount, formatCompact, formatDate, formatPercent, formatRatio, ordinal } from "../lib/format";
import { useAppState } from "../state/AppState";

const LEVEL_MEANING: Record<string, string> = {
  minimal: "Too little burning to matter, whatever the season says.",
  below_normal: "Less burning than three quarters of other years at this point.",
  normal: "Within the middle half of other years.",
  elevated: "More burning than three quarters of other years.",
  high: "More burning than nine in ten other years.",
  extreme: "Among the highest ever recorded for this time of year.",
};

function recentOption(data: WarningResponse): ChartOption {
  const r = data.recent_daily;
  return {
    grid: { left: 60, right: 18, top: LEGEND_GRID_TOP, bottom: 30 },
    legend: { ...LEGEND_STYLE, data: ["Daily activity", "Typical (median)", "90th percentile"] },
    tooltip: {
      ...TOOLTIP_STYLE,
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params: unknown) => {
        const i = (params as { dataIndex: number }[])[0]?.dataIndex ?? 0;
        const type = VALUE_TYPE_BY_CODE[r.vtype[i] ?? 0] ?? "missing";
        return [
          `<strong>${formatDate(r.dates[i])}</strong>`,
          r.values[i] === null || r.values[i] === undefined ? "No data ingested for this day" : `${formatAmount(r.values[i])} ${data.metric.unit_short} (${VALUE_TYPE_SHORT[type].toLowerCase()})`,
          `Typical for the date: ${formatAmount(r.climatology_median[i])}`,
          `90th percentile: ${formatAmount(r.climatology_p90[i])}`,
        ].join("<br/>");
      },
    },
    xAxis: { type: "category", data: r.dates, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, formatter: (d: string) => formatDate(d, "short"), interval: 9 } },
    yAxis: { type: "value", name: `${data.metric.unit_short} per day`, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, formatter: (v: number) => formatCompact(v) } },
    series: [
      {
        name: "Daily activity",
        type: "bar",
        barCategoryGap: "18%",
        data: r.values.map((v, i) => ({ value: v, itemStyle: { color: VALUE_TYPE_COLORS[VALUE_TYPE_BY_CODE[r.vtype[i] ?? 0] ?? "missing"] } })),
        itemStyle: { color: THEME.cool },
      },
      { name: "Typical (median)", type: "line", data: r.climatology_median, symbol: "none", lineStyle: { color: THEME.text, width: 1.6 }, itemStyle: { color: THEME.text } },
      { name: "90th percentile", type: "line", data: r.climatology_p90, symbol: "none", lineStyle: { color: THEME.fire, width: 1.4, type: "dashed" }, itemStyle: { color: THEME.fire } },
    ],
  };
}

function historyOption(window: RecentWindow, data: WarningResponse): ChartOption {
  const year = Number(data.as_of.slice(0, 4));
  const rows = [...(window.history ?? []), ...(window.value !== null ? [{ year, value: window.value }] : [])].sort((a, b) => a.year - b.year);
  return {
    grid: { left: 60, right: 62, top: 14, bottom: 30 },
    tooltip: {
      ...TOOLTIP_STYLE,
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params: unknown) => {
        const p = (params as { dataIndex: number }[])[0];
        const row = rows[p?.dataIndex ?? 0];
        return row ? `<strong>${row.year}</strong><br/>Same ${window.days} days: ${formatAmount(row.value)} ${data.metric.unit_short}` : "";
      },
    },
    xAxis: { type: "category", data: rows.map((r) => String(r.year)), ...AXIS_STYLE },
    yAxis: { type: "value", ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, formatter: (v: number) => formatCompact(v) } },
    series: [
      {
        type: "bar",
        barMaxWidth: 22,
        data: rows.map((r) => ({ value: r.value, itemStyle: { color: r.year === year ? (data.level ? LEVEL_COLORS[data.level] : THEME.text) : "#3b4756" } })),
        markLine:
          window.reference_median !== null && window.reference_median !== undefined
            ? { silent: true, symbol: "none", lineStyle: { color: THEME.text, type: "dashed" }, label: { color: THEME.muted, fontSize: 10, formatter: "Median" }, data: [{ yAxis: window.reference_median }] }
            : undefined,
      },
    ],
  };
}

function progressOption(progress: SeasonProgress, unit: string): ChartOption | null {
  const env = progress.envelope;
  if (!env) return null;
  const diff = (hi: number[], lo: number[]) => hi.map((v, i) => v - (lo[i] ?? 0));
  const current: (number | null)[] = env.offsets.map(() => null);
  if (progress.current) {
    const lookup = new Map(progress.current.offsets.map((o, i) => [o, progress.current?.values[i] ?? null]));
    env.offsets.forEach((o, i) => {
      if (lookup.has(o)) current[i] = lookup.get(o) ?? null;
    });
    // Make sure the last known point is drawn even if it falls between envelope samples.
    const lastOffset = progress.current.offsets[progress.current.offsets.length - 1];
    const lastValue = progress.current.values[progress.current.values.length - 1];
    if (lastOffset !== undefined && lastValue !== undefined) {
      let nearest = 0;
      env.offsets.forEach((o, i) => {
        if (Math.abs(o - lastOffset) < Math.abs((env.offsets[nearest] ?? 0) - lastOffset)) nearest = i;
      });
      current[nearest] = lastValue;
    }
  }
  return {
    grid: { left: 64, right: 18, top: LEGEND_GRID_TOP, bottom: 30 },
    legend: { ...LEGEND_STYLE, data: ["Median of other years", "25 to 75 percent", "10 to 90 percent", ...(progress.current ? [`Season ${progress.season_year}`] : [])] },
    tooltip: {
      ...TOOLTIP_STYLE,
      trigger: "axis",
      formatter: (params: unknown) => {
        const i = (params as { dataIndex: number }[])[0]?.dataIndex ?? 0;
        const lines = [
          `<strong>${formatDate(env.dates[i], "short")}</strong>`,
          `Median so far: ${formatAmount(env.p50[i])}`,
          `10 to 90 percent: ${formatAmount(env.p10[i])} to ${formatAmount(env.p90[i])}`,
        ];
        if (current[i] !== null && current[i] !== undefined) lines.push(`Season ${progress.season_year}: ${formatAmount(current[i])}`);
        return lines.join("<br/>");
      },
    },
    xAxis: { type: "category", data: env.dates, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, formatter: (d: string) => formatDate(d, "short"), interval: Math.max(1, Math.floor(env.dates.length / 8)) } },
    yAxis: { type: "value", name: `cumulative ${unit}`, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, formatter: (v: number) => formatCompact(v) } },
    series: [
      { name: "p10", type: "line", data: env.p10, stack: "outer", symbol: "none", lineStyle: { opacity: 0 }, silent: true },
      { name: "10 to 90 percent", type: "line", data: diff(env.p90, env.p10), stack: "outer", symbol: "none", lineStyle: { opacity: 0 }, areaStyle: { color: "rgba(255,255,255,0.08)" }, itemStyle: { color: "rgba(255,255,255,0.25)" } },
      { name: "p25", type: "line", data: env.p25, stack: "inner", symbol: "none", lineStyle: { opacity: 0 }, silent: true },
      { name: "25 to 75 percent", type: "line", data: diff(env.p75, env.p25), stack: "inner", symbol: "none", lineStyle: { opacity: 0 }, areaStyle: { color: "rgba(255,255,255,0.14)" }, itemStyle: { color: "rgba(255,255,255,0.45)" } },
      { name: "Median of other years", type: "line", data: env.p50, symbol: "none", lineStyle: { color: THEME.muted, width: 1.6 }, itemStyle: { color: THEME.muted } },
      ...(progress.current
        ? [{ name: `Season ${progress.season_year}`, type: "line", data: current, symbol: "none", connectNulls: true, lineStyle: { color: THEME.fire, width: 2.6 }, itemStyle: { color: THEME.fire } }]
        : []),
    ],
  };
}

function OutlookCard({ item, unit }: { item: OutlookItem; unit: string }) {
  const max = Math.max(item.upper ?? 0, item.climatology_p90 ?? 0, item.expected ?? 0, 1);
  const pct = (v: number | null | undefined) => `${Math.min(100, Math.max(0, ((v ?? 0) / max) * 100))}%`;
  return (
    <div className="outlook">
      <div className="outlook-head">
        <span className="outlook-when">
          {formatDate(item.start, "short")} to {formatDate(item.end, "short")}
        </span>
        <span className="muted small">week {item.horizon_weeks} ahead</span>
      </div>
      {!item.available ? (
        <Notice tone="info">{item.reason ?? "Not available."}</Notice>
      ) : (
        <>
          <div className="outlook-value">
            {formatAmount(item.expected)} <span className="outlook-unit">{unit}</span>
          </div>
          <div className="muted small">
            80 percent interval {formatAmount(item.lower)} to {formatAmount(item.upper)}
          </div>
          <div className="range" role="img" aria-label={`Expected ${formatAmount(item.expected)}, interval ${formatAmount(item.lower)} to ${formatAmount(item.upper)}, climatological median ${formatAmount(item.climatology_median)}`}>
            <span className="range-band" style={{ left: pct(item.lower), width: `calc(${pct(item.upper)} - ${pct(item.lower)})` }} />
            <span className="range-mark range-expected" style={{ left: pct(item.expected) }} />
            <span className="range-mark range-clim" style={{ left: pct(item.climatology_median) }} />
          </div>
          <div className="legend-row">
            <Swatch color={THEME.fire} label="Expected" />
            <Swatch color={THEME.text} label={`Typical for these dates: ${formatAmount(item.climatology_median)}`} />
          </div>
          <p className="small">
            Method: {item.method_label}.{" "}
            {item.method === "persistence_scaled_climatology"
              ? `Recent activity is ${formatRatio(item.recent_anomaly_ratio)} the norm and ${formatPercent(item.persistence ?? 0)} of that anomaly typically persists. In ${item.hindcast_pairs} hindcasts this beat climatology by ${formatPercent(item.skill_vs_climatology ?? 0)}.`
              : (item.fallback_reason ?? "")}
          </p>
        </>
      )}
    </div>
  );
}

function WarningView({ data }: { data: WarningResponse }) {
  const { update } = useAppState();
  const [copied, setCopied] = useState(false);
  const recent = useMemo(() => recentOption(data), [data]);
  const slug = fileSlug(data.aoi.name);
  const history = useMemo(() => historyOption(data.last_7_days, data), [data]);
  const progress = useMemo(
    () => (data.season_progress ? progressOption(data.season_progress, data.metric.unit_short) : null),
    [data],
  );
  const week = data.last_7_days;
  const fortnight = data.last_14_days;
  const month = data.month_to_date;
  const status = data.critical_period_status;
  const bounds = data.as_of_bounds;
  const levelColor = data.level ? LEVEL_COLORS[data.level] : THEME.faint;
  const bulletinText = data.bulletin.join("\n\n");

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(bulletinText);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2500);
    } catch {
      setCopied(false);
    }
  };
  const download = () => {
    const blob = new Blob([bulletinText, "\n"], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `agnisetu_bulletin_${data.aoi.id}_${data.as_of}.txt`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };
  const move = (days: number) => update({ asOf: clampDate(addDays(data.as_of, days), bounds.min, bounds.max) });

  return (
    <>
      <Card
        title="Status as of"
        subtitle="Pick any date in the record to see what this page would have shown that day. Data from later in that same year is never used."
      >
        <div className="toolbar">
          <div className="date-row">
            <button type="button" className="btn" onClick={() => move(-7)} disabled={data.as_of <= bounds.min}>
              Back 7 days
            </button>
            <button type="button" className="btn btn-icon" onClick={() => move(-1)} disabled={data.as_of <= bounds.min} aria-label="Previous day">
              &lt;
            </button>
            <label className="sr-only" htmlFor="as-of">
              As of date
            </label>
            <input
              id="as-of"
              type="date"
              value={data.as_of}
              min={bounds.min}
              max={bounds.max}
              onChange={(event) => event.target.value && update({ asOf: clampDate(event.target.value, bounds.min, bounds.max) })}
            />
            <button type="button" className="btn btn-icon" onClick={() => move(1)} disabled={data.as_of >= bounds.max} aria-label="Next day">
              &gt;
            </button>
            <button type="button" className="btn" onClick={() => move(7)} disabled={data.as_of >= bounds.max}>
              Forward 7 days
            </button>
          </div>
          <button type="button" className="btn" onClick={() => update({ asOf: null })} disabled={data.is_latest}>
            Latest complete day ({formatDate(bounds.default, "short")})
          </button>
        </div>

        <div className="alert" style={{ borderColor: levelColor }}>
          <div className="alert-level" style={{ color: levelColor }}>
            {data.level_label ?? "Withheld"}
          </div>
          <div className="alert-body">
            <p className="alert-headline">{data.bulletin[0]}</p>
            <p className="muted small">
              {data.level_rule ?? week.reason ?? "Recent days are not ingested."}
              {data.level ? ` ${LEVEL_MEANING[data.level] ?? ""}` : ""}
            </p>
            <ol className="level-scale" aria-label="Alert scale">
              {data.level_scale.map((l) => (
                <li
                  key={l.key}
                  className={l.key === data.level ? "is-current" : ""}
                  style={{ background: l.key === data.level ? LEVEL_COLORS[l.key] : undefined, borderColor: LEVEL_COLORS[l.key] }}
                  aria-current={l.key === data.level ? "true" : undefined}
                >
                  {l.label}
                </li>
              ))}
            </ol>
          </div>
        </div>

        <div className="stat-grid">
          <Stat
            label={`Last 7 days (${formatDate(week.start, "short")} to ${formatDate(week.end, "short")})`}
            value={week.available ? formatAmount(week.value) : "Withheld"}
            hint={week.available ? `${data.metric.unit_short}; typical ${formatAmount(week.reference_median)}` : week.reason}
          />
          <Stat
            label="Against the same week in other years"
            value={week.available && week.percentile !== null && week.percentile !== undefined ? `${ordinal(week.percentile)} percentile` : "n/a"}
            hint={week.available ? `${formatRatio(week.ratio_to_median)} the median of ${week.reference_years} years` : undefined}
          />
          <Stat
            label="Last 14 days"
            value={fortnight.available ? formatAmount(fortnight.value) : "Withheld"}
            hint={
              fortnight.available && fortnight.percentile !== null && fortnight.percentile !== undefined
                ? `${ordinal(fortnight.percentile)} percentile; typical ${formatAmount(fortnight.reference_median)}`
                : fortnight.reason
            }
          />
          <Stat
            label={`${month.month_name} to date (${month.days_elapsed} of ${month.days_in_month} days)`}
            value={month.available ? formatAmount(month.value) : "Withheld"}
            hint={
              month.available && month.percentile !== null && month.percentile !== undefined
                ? `${ordinal(month.percentile)} percentile for the same days in other years, ${formatRatio(month.ratio_to_median)} the median. A whole ${month.month_name} is typically ${formatAmount(month.full_month_median)}`
                : month.reason
            }
          />
          <Stat
            label={status?.status === "in_progress" ? "Critical period under way" : "Next critical period"}
            value={
              status
                ? status.status === "in_progress"
                  ? `Day ${status.day_of_period} of about ${status.typical_length_days}`
                  : `In ${status.days_until} days`
                : "None defined"
            }
            hint={
              status
                ? status.status === "in_progress"
                  ? `${status.period_name}; usually ends around ${formatDate(status.typical_end, "short")}`
                  : `${status.period_name}; usually starts around ${formatDate(status.typical_onset, "short")}, earliest on record ${status.earliest_onset ?? "n/a"}`
                : undefined
            }
          />
        </div>
        {!data.reference_observed_recently && (data.recent_sources.viirs_calibrated > 0 || data.recent_sources.modis_harmonized > 0) && (
          <Notice tone="warn">
            The reference sensor (VIIRS S-NPP) did not report in this window.{" "}
            {data.recent_sources.viirs_calibrated > 0
              ? `${data.recent_sources.viirs_calibrated} of the last 7 days come from VIIRS NOAA-20, calibrated to S-NPP.`
              : `${data.recent_sources.modis_harmonized} of the last 7 days are estimates from MODIS.`}
          </Notice>
        )}
      </Card>

      <div className="chart-grid">
        <Card title="Last 60 days" subtitle="Daily activity against what is typical for each date. Bar color shows which sensor the value comes from.">
          <EChart option={recent} height={280} ariaLabel="Daily burning activity over the last 60 days with the typical level" downloadName={`agnisetu_last_60_days_${slug}`} />
          <div className="legend-row">
            {(["reference", "viirs_calibrated", "modis_harmonized"] as const).map((type) => (
              <Swatch key={type} color={VALUE_TYPE_COLORS[type]} label={VALUE_TYPE_SHORT[type]} />
            ))}
          </div>
        </Card>
        <Card title="The same 7 days in every year" subtitle={`${formatDate(week.start, "short")} to ${formatDate(week.end, "short")}, harmonized.`}>
          {(week.history?.length ?? 0) > 0 ? (
            <EChart option={history} height={280} ariaLabel="Activity in the same calendar week for every year of the record" downloadName={`agnisetu_same_week_every_year_${slug}`} />
          ) : (
            <Notice tone="info">No other year has data for this calendar window.</Notice>
          )}
        </Card>
      </div>

      <Card
        title="Season to date"
        subtitle={
          data.season_progress
            ? `${data.season_progress.period_name}, season ${data.season_progress.season_year}: cumulative activity since ${formatDate(data.season_progress.window_start, "short")} against ${data.season_progress.reference_years.length} other years.`
            : "Cumulative activity inside the current critical period."
        }
      >
        {!data.season_progress ? (
          <Notice tone="info">
            {formatDate(data.as_of)} is outside every critical period for this area, so there is no season in progress.
          </Notice>
        ) : (
          <>
            {data.season_progress.available ? (
              <div className="stat-grid">
                <Stat label="Season so far" value={formatAmount(data.season_progress.to_date)} hint={`typical by this date: ${formatAmount(data.season_progress.to_date_median)}`} />
                <Stat
                  label="Against other years at this point"
                  value={data.season_progress.to_date_percentile !== null && data.season_progress.to_date_percentile !== undefined ? `${ordinal(data.season_progress.to_date_percentile)} percentile` : "n/a"}
                />
                <Stat label="Share of a typical whole season" value={formatPercent(data.season_progress.share_of_typical_season)} hint={`day ${data.season_progress.days_elapsed} of ${data.season_progress.days_total} in the window`} />
              </div>
            ) : (
              <Notice tone="warn">
                {data.season_progress.reason}
                {!data.has_map_key && data.is_latest
                  ? " Set AGNISETU_FIRMS_MAP_KEY and run the backfill command described on the Data and Methods tab."
                  : ""}
              </Notice>
            )}
            {progress && <EChart option={progress} height={300} ariaLabel="Cumulative season activity against the envelope of other years" downloadName={`agnisetu_season_to_date_${slug}`} />}
          </>
        )}
      </Card>

      <Card
        title="Outlook for the next two weeks"
        subtitle="A regional statistical expectation with a measured track record. It does not predict where or when individual fires will start."
      >
        <div className="outlook-grid">
          {data.outlook.map((item) => (
            <OutlookCard key={item.horizon_weeks} item={item} unit={data.metric.unit_short} />
          ))}
        </div>
      </Card>

      <Card
        title="Bulletin"
        subtitle="Built from the numbers above by fixed sentence templates. No language model is involved."
        actions={
          <>
            <button type="button" className="btn" onClick={() => void copy()} aria-live="polite">
              {copied ? "Copied" : "Copy text"}
            </button>
            <button type="button" className="btn" onClick={download}>
              Download .txt
            </button>
          </>
        }
      >
        <div className="bulletin">
          {data.bulletin.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </div>
      </Card>
    </>
  );
}

export function WarningTab() {
  const query = useWarning();
  return (
    <div className="tab-stack">
      {query.isFetching && query.data && (
        <div className="floating-status">
          <Spinner label="Recomputing for the new date" /> Recomputing
        </div>
      )}
      <QueryState query={query} loadingLabel="Comparing recent activity with every other year" height={420}>
        {(data) => <WarningView data={data} />}
      </QueryState>
    </div>
  );
}
