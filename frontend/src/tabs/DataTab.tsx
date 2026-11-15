import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import { useAoi, useDataCoverage, useMeta, useOptions } from "../api/hooks";
import type { ArchiveEntry, DataCoverageResponse, Job } from "../api/types";
import { Card, describeError, Notice, QueryState, Spinner, Stat } from "../components/ui";
import { PLATFORM_COLORS } from "../lib/colors";
import { addDays, formatBytes, formatDate, formatNumber, formatPercent, formatTimestamp } from "../lib/format";

type SortKey = "dataset" | "country" | "year" | "rows" | "rows_rejected" | "bytes";
const PAGE_SIZE = 20;
const DATASET_LABELS: Record<string, string> = {
  modis: "MODIS Collection 6.1 (Terra, Aqua)",
  "viirs-snpp": "VIIRS S-NPP Collection 2",
  "viirs-jpss1": "VIIRS NOAA-20 Collection 2",
};

function ArchiveTable({ data }: { data: DataCoverageResponse }) {
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "year", dir: -1 });
  const [dataset, setDataset] = useState("all");
  const [country, setCountry] = useState("all");
  const [page, setPage] = useState(0);
  const datasets = useMemo(() => [...new Set(data.archive.map((e) => e.dataset))].sort(), [data.archive]);
  const countries = useMemo(() => [...new Set(data.archive.map((e) => e.country))].sort(), [data.archive]);
  const rows = useMemo(() => {
    const filtered = data.archive.filter((e) => (dataset === "all" || e.dataset === dataset) && (country === "all" || e.country === country));
    return filtered.sort((a, b) => {
      const av = a[sort.key];
      const bv = b[sort.key];
      const primary = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
      return (primary || a.key.localeCompare(b.key)) * sort.dir;
    });
  }, [data.archive, dataset, country, sort]);
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const current = Math.min(page, pages - 1);
  const visible = rows.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE);

  const header = (key: SortKey, label: string, numeric = false) => (
    <th scope="col" className={numeric ? "num" : undefined} aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      <button
        type="button"
        className="sort-button"
        onClick={() => {
          setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : numeric ? -1 : 1 }));
          setPage(0);
        }}
      >
        {label}
        <span aria-hidden="true">{sort.key === key ? (sort.dir === 1 ? " ↑" : " ↓") : ""}</span>
      </button>
    </th>
  );

  return (
    <>
      <div className="toolbar">
        <label className="select-field">
          <span className="control-label">Dataset</span>
          <select value={dataset} onChange={(e) => { setDataset(e.target.value); setPage(0); }}>
            <option value="all">All datasets</option>
            {datasets.map((d) => (
              <option key={d} value={d}>
                {DATASET_LABELS[d] ?? d}
              </option>
            ))}
          </select>
        </label>
        <label className="select-field">
          <span className="control-label">Country</span>
          <select value={country} onChange={(e) => { setCountry(e.target.value); setPage(0); }}>
            <option value="all">All countries</option>
            {countries.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <span className="muted small">{formatNumber(rows.length)} files</span>
      </div>
      <div className="table-scroll">
        <table className="table">
          <caption className="sr-only">Ingested archive files with provenance</caption>
          <thead>
            <tr>
              {header("dataset", "Dataset")}
              {header("country", "Country")}
              {header("year", "Year", true)}
              {header("rows", "Rows", true)}
              {header("rows_rejected", "Rejected", true)}
              {header("bytes", "Source size", true)}
              <th scope="col">Dates</th>
              <th scope="col">SHA-256</th>
              <th scope="col">Source</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((e: ArchiveEntry) => (
              <tr key={e.key}>
                <td>{DATASET_LABELS[e.dataset] ?? e.dataset}</td>
                <td>{e.country}</td>
                <td className="num">{e.year}</td>
                <td className="num">{formatNumber(e.rows)}</td>
                <td className="num">{formatNumber(e.rows_rejected)}</td>
                <td className="num">{formatBytes(e.bytes)}</td>
                <td>
                  {formatDate(e.min_date, "short")} to {formatDate(e.max_date, "short")}
                </td>
                <td>
                  <code title={e.sha256}>{e.sha256.slice(0, 12)}</code>
                </td>
                <td>
                  <a href={e.url} target="_blank" rel="noopener noreferrer">
                    FIRMS file
                  </a>
                </td>
              </tr>
            ))}
            {visible.length === 0 && (
              <tr>
                <td colSpan={9} className="muted">
                  No files match these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="pager">
        <button type="button" className="btn" onClick={() => setPage(current - 1)} disabled={current === 0}>
          Previous
        </button>
        <span className="small">
          Page {current + 1} of {pages}
        </span>
        <button type="button" className="btn" onClick={() => setPage(current + 1)} disabled={current >= pages - 1}>
          Next
        </button>
      </div>
    </>
  );
}

function Operations({ jobs }: { jobs: Job[] }) {
  const client = useQueryClient();
  const meta = useMeta();
  const [token, setToken] = useState("");
  const [start, setStart] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "info" | "danger"; text: string } | null>(null);

  const run = async (action: () => Promise<{ job: Job }>, label: string) => {
    if (!token) {
      setMessage({ tone: "danger", text: "Enter the admin token first." });
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const { job } = await action();
      setMessage({ tone: "info", text: `${label} started (job ${job.id}). This page refreshes when it finishes.` });
      const poll = window.setInterval(async () => {
        try {
          const { jobs: list } = await api.adminJobs(token);
          const found = list.find((j) => j.id === job.id);
          if (found && found.state !== "running") {
            window.clearInterval(poll);
            setMessage({
              tone: found.state === "succeeded" ? "info" : "danger",
              text: found.state === "succeeded" ? `${label} finished.` : `${label} failed: ${found.error ?? "see the server log"}`,
            });
            void client.invalidateQueries();
          }
        } catch {
          window.clearInterval(poll);
        }
      }, 3000);
    } catch (error) {
      setMessage({ tone: "danger", text: describeError(error).detail });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="Operations"
      subtitle="Requires the admin token configured on the server. The token is sent only with these requests and is not stored."
    >
      <div className="toolbar">
        <label className="select-field">
          <span className="control-label">Admin token</span>
          <input type="password" value={token} autoComplete="off" onChange={(e) => setToken(e.target.value)} />
        </label>
        <button type="button" className="btn" disabled={busy} onClick={() => void run(() => api.adminRefresh(token), "Near real time refresh")}>
          Refresh 7 day feeds now
        </button>
        <label className="select-field">
          <span className="control-label">Backfill from (optional)</span>
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <button
          type="button"
          className="btn"
          disabled={busy || !meta.data?.has_map_key}
          title={meta.data?.has_map_key ? undefined : "The server has no FIRMS MAP_KEY configured"}
          onClick={() => void run(() => api.adminBackfill(token, start || null, null), "API backfill")}
        >
          Backfill through the FIRMS API
        </button>
        {busy && <Spinner label="Starting job" />}
      </div>
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      {jobs.length > 0 && (
        <div className="table-scroll">
          <table className="table">
            <caption className="sr-only">Recent ingestion jobs</caption>
            <thead>
              <tr>
                <th scope="col">Job</th>
                <th scope="col">Trigger</th>
                <th scope="col">State</th>
                <th scope="col">Started</th>
                <th scope="col">Finished</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id}>
                  <td>{j.kind === "refresh_nrt" ? "Feed refresh" : "API backfill"}</td>
                  <td>{j.trigger}</td>
                  <td>{j.state}</td>
                  <td>{formatTimestamp(j.started_at)}</td>
                  <td>{formatTimestamp(j.finished_at)}</td>
                  <td>{j.error ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function Exports() {
  const aoi = useAoi();
  const options = useOptions();
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const run = async (kind: "daily.csv" | "calendar.csv" | "report.json") => {
    if (!aoi.ref) return;
    setBusy(kind);
    setProblem(null);
    try {
      await api.download(kind, aoi.ref, options);
    } catch (error) {
      setProblem(describeError(error).detail);
    } finally {
      setBusy(null);
    }
  };
  return (
    <Card
      title="Export"
      subtitle={aoi.info ? `For ${aoi.info.name}, with the metric and confidence filter chosen in the header.` : "Choose an area first."}
    >
      <div className="toolbar">
        <button type="button" className="btn" disabled={!aoi.ref || busy !== null} onClick={() => void run("daily.csv")}>
          Harmonized daily series (CSV)
        </button>
        <button type="button" className="btn" disabled={!aoi.ref || busy !== null} onClick={() => void run("calendar.csv")}>
          Weekly calendar (CSV)
        </button>
        <button type="button" className="btn" disabled={!aoi.ref || busy !== null} onClick={() => void run("report.json")}>
          Full analysis report (JSON)
        </button>
        {busy && <Spinner label="Preparing the file" />}
      </div>
      {problem && <Notice tone="danger">{problem}</Notice>}
      <p className="muted small">
        The daily file has one row per day from November 2000 with the harmonized value, its 80 percent interval, the
        value type, and the raw value and coverage flag of every sensor.
      </p>
    </Card>
  );
}

export function DataTab() {
  const meta = useMeta();
  const query = useDataCoverage();
  return (
    <div className="tab-stack">
      <Exports />
      <QueryState query={query} loadingLabel="Reading the ingestion manifest" height={300}>
        {(data) => (
          <>
            <Card title="What is in the store" subtitle={`Countries: ${data.summary.countries.join(", ")}. Store revision ${data.revision}.`}>
              <div className="stat-grid">
                <Stat label="Archive files" value={formatNumber(data.archive_totals.files)} hint={formatBytes(data.archive_totals.bytes)} />
                <Stat label="Detections ingested" value={formatNumber(data.archive_totals.rows)} hint="standard quality archive" />
                <Stat label="Rows rejected" value={formatNumber(data.archive_totals.rows_rejected)} hint={`${formatPercent(data.archive_totals.rejected_share, 5)} failed type or range checks`} />
                <Stat label="Static source cells" value={formatNumber(data.static_mask?.cells ?? 0)} hint={`${data.static_mask?.cell_deg ?? 0.01} degree mask from the archive type flag`} />
              </div>
              <div className="table-scroll">
                <table className="table">
                  <caption className="sr-only">Coverage by sensor</caption>
                  <thead>
                    <tr>
                      <th scope="col">Sensor</th>
                      <th scope="col">Pixel</th>
                      <th scope="col">Local overpass</th>
                      <th scope="col">First observed</th>
                      <th scope="col">Last complete day</th>
                      <th scope="col" className="num">Days observed</th>
                      <th scope="col" className="num">Outage days</th>
                      <th scope="col" className="num">Detections</th>
                      <th scope="col">Archive through</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.summary.platforms.map((p) => (
                      <tr key={p.key}>
                        <th scope="row">
                          <span className="check-dot" style={{ background: PLATFORM_COLORS[p.key] }} aria-hidden="true" /> {p.label}
                        </th>
                        <td>{p.resolution_m} m</td>
                        <td>{p.overpass_local}</td>
                        <td>{formatDate(p.first_observed)}</td>
                        <td>{formatDate(p.last_observed)}</td>
                        <td className="num">{formatNumber(p.days_observed)}</td>
                        <td className="num">{formatNumber(p.days_outage)}</td>
                        <td className="num">{formatNumber(p.detections)}</td>
                        <td>{formatDate(p.archive_through)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>

            <Card
              title="Near real time"
              subtitle={`Rolling 7 day feeds are pulled without a key${meta.data?.nrt_refresh_minutes ? ` every ${meta.data.nrt_refresh_minutes} minutes while the server runs` : ""}. The first and last day of each pull are partial and stay provisional.`}
            >
              <div className="table-scroll">
                <table className="table">
                  <caption className="sr-only">Near real time ingestion by sensor</caption>
                  <thead>
                    <tr>
                      <th scope="col">Sensor</th>
                      <th scope="col">First day</th>
                      <th scope="col">Last day</th>
                      <th scope="col">Last complete day</th>
                      <th scope="col" className="num">Day files</th>
                      <th scope="col" className="num">Detections</th>
                      <th scope="col">Source</th>
                      <th scope="col">Updated</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.near_real_time.map((n) => {
                      const newest = data.near_real_time.reduce((best, row) => (row.last_day && row.last_day > best ? row.last_day : best), "");
                      const silent = n.last_day !== null && newest !== "" && n.last_day < addDays(newest, -2);
                      return (
                      <tr key={n.platform}>
                        <th scope="row">{n.label}</th>
                        <td>{formatDate(n.first_day)}</td>
                        <td>{formatDate(n.last_day)}</td>
                        <td>{formatDate(n.last_complete_day)}</td>
                        <td className="num">{formatNumber(n.days)}</td>
                        <td className="num">{formatNumber(n.rows)}</td>
                        <td>
                          {n.days === 0 ? "Feed returned no detections" : n.sources.map((s) => (s === "api" ? "FIRMS API" : "7 day feed")).join(", ")}
                          {silent ? `. Silent since ${formatDate(n.last_day)}` : ""}
                        </td>
                        <td>{formatTimestamp(n.updated_at)}</td>
                      </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {data.not_published.length > 0 && (
                <Notice tone="info">
                  The yearly archive for the current year is not published yet ({data.not_published.length} files checked).
                  Days between the end of the archive and the 7 day feed are not ingested
                  {meta.data?.has_map_key
                    ? ". A FIRMS MAP_KEY is configured: run the backfill to fill them."
                    : ". To fill them, request a free key at firms.modaps.eosdis.nasa.gov/api/map_key, set AGNISETU_FIRMS_MAP_KEY in the .env file, and run: python -m agnisetu.cli backfill-api"}
                </Notice>
              )}
            </Card>

            <Card title="Archive files" subtitle="Every file is hashed when downloaded. A file that fails the header, type or year checks is rejected whole.">
              <ArchiveTable data={data} />
            </Card>

            {meta.data?.admin_enabled && <Operations jobs={data.jobs} />}
          </>
        )}
      </QueryState>

      <Card title="Method in brief">
        <dl className="method">
          <dt>Unit</dt>
          <dd>
            Everything is expressed as VIIRS S-NPP equivalent activity. S-NPP has the longest 375 m record and 14 years
            of overlap with MODIS.
          </dd>
          <dt>Days and weeks</dt>
          <dd>
            Detections are assigned to the local solar day (UTC time plus four minutes per degree of longitude), so a
            night overpass at 01:30 is not counted on the previous day. A year has 52 bins of 7 days; the last bin holds
            the remaining 8 or 9 days and all bins are scaled to 7 days.
          </dd>
          <dt>Quality</dt>
          <dd>
            Only detections typed as vegetation fire count. Static heat sources are removed with the archive type flag,
            and for near real time data with a 0.01 degree mask derived from it. Low confidence detections are excluded
            by default.
          </dd>
          <dt>Coverage</dt>
          <dd>
            Each sensor is in one state each day: before launch, not ingested, outage, observed or provisional. A
            silent run of days is an outage when the detections that sensor normally reports on those calendar days
            (the median of other years) add up to at least 20. A quiet season therefore explains its own zeros, and a
            sensor that stops reporting in a busy season does not.
          </dd>
          <dt>Calibration</dt>
          <dd>
            Weekly rates of the predictor sensors and of S-NPP are matched over days when all were observing. Five
            candidates are compared with leave one year out cross validation: no correction, a single ratio, a seasonal
            ratio, a harmonic quasi Poisson model, and a platform aware variant. The simplest model within 2 percent of
            the best weekly error is refitted on all overlap data.
          </dd>
          <dt>Intervals</dt>
          <dd>
            The 10th and 90th percentile of held out residuals on the log scale, by predicted magnitude, give an
            empirical 80 percent interval for every estimate.
          </dd>
          <dt>Critical periods</dt>
          <dd>
            The smallest set of weeks that holds 80 percent of mean annual activity after light smoothing, grouped into
            continuous periods. Periods under 5 percent of annual activity are dropped.
          </dd>
          <dt>Anomalies</dt>
          <dd>
            Each season is compared with every other complete year: ratio to the median, mid rank percentile and a
            robust z score from the median absolute deviation. Trends use the Theil-Sen slope and the Mann-Kendall test.
          </dd>
          <dt>Outlook</dt>
          <dd>
            Climatological median scaled by the recent 14 day anomaly raised to a persistence exponent fitted on other
            years. It is used only when it beat climatology by at least 5 percent in hindcasts; otherwise the outlook is
            the climatology itself.
          </dd>
        </dl>
      </Card>

      <Card title="What this tool does not claim">
        <ul className="plain-list">
          <li>Values before 2012 are statistical estimates of what VIIRS S-NPP would have recorded. They are not observations and cannot recover individual fires that MODIS did not detect.</li>
          <li>Active fire detections are snapshots at overpass time. Fires that start and end between overpasses, or burn under thick cloud or smoke, are not counted by any sensor. Burning timed to avoid the afternoon overpass lowers counts without lowering smoke.</li>
          <li>Detections are not burned area and not emissions.</li>
          <li>Near real time data is provisional and is replaced by standard quality data when the yearly archive is published.</li>
          <li>The outlook is a regional statistical expectation with a measured skill. It is not an ignition forecast.</li>
          <li>Some industrial heat sources may remain after filtering, and some fires next to them may be removed.</li>
          <li>Boundaries are used only to select analysis areas and do not imply any position on territorial status.</li>
        </ul>
      </Card>

      <Card title="Sources and credits">
        <ul className="plain-list">
          <li>
            Active fire data: NASA Fire Information for Resource Management System (FIRMS), MODIS Collection 6.1 and VIIRS
            Collection 2, part of NASA Earth Science Data and Information System (ESDIS).{" "}
            <a href="https://firms.modaps.eosdis.nasa.gov" target="_blank" rel="noopener noreferrer">
              firms.modaps.eosdis.nasa.gov
            </a>
          </li>
          <li>Imagery: NASA Global Imagery Browse Services (GIBS). Street map: OpenStreetMap contributors.</li>
          {(meta.data?.boundary_sources ?? []).map((b) => (
            <li key={`${b.country}-${b.level}`}>
              {b.country} {b.level === "ADM1" ? "state and province" : "district and department"} boundaries: {b.source}, via geoBoundaries ({b.license}).
            </li>
          ))}
          <li>AgniSetu {meta.data?.version ?? ""} was built for the NASA Space Apps Challenge 2026, Delhi.</li>
        </ul>
      </Card>
    </div>
  );
}
