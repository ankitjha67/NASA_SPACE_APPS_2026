import { useState } from "react";
import { useAoi, useMeta } from "../api/hooks";
import type { ConfidenceFilter, Metric } from "../api/types";
import { formatArea, formatDate } from "../lib/format";
import { useAppState } from "../state/AppState";
import { TABS, type TabKey } from "../state/urlState";
import { Segmented } from "./ui";

export function Header() {
  const { state, update } = useAppState();
  const meta = useMeta();
  const aoi = useAoi();
  const [copied, setCopied] = useState<"idle" | "done" | "failed">("idle");

  const share = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied("done");
    } catch {
      setCopied("failed");
    }
    window.setTimeout(() => setCopied("idle"), 2500);
  };

  const coverage = meta.data?.coverage;
  const snpp = coverage?.platforms.find((p) => p.key === "SNPP");
  const referenceSilent =
    coverage?.ready && snpp?.last_observed && coverage.last_complete_day && snpp.last_observed < coverage.last_complete_day;

  return (
    <header className="app-header">
      <div className="brand">
        <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
          <path d="M4 22c4-9 8-9 12 0s8 9 12 0" fill="none" stroke="#4fb3ff" strokeWidth="2.4" strokeLinecap="round" />
          <path d="M16 5c3 4 5 6.5 5 10a5 5 0 0 1-10 0c0-2 1-3.4 2.2-4.6.3 1.6 1 2.4 1.9 2.6C14.6 10.5 14.9 8 16 5z" fill="#ff8a3d" />
        </svg>
        <div>
          <h1 className="brand-name">AgniSetu</h1>
          <p className="brand-tag">Harmonized MODIS and VIIRS burning activity calendar</p>
        </div>
      </div>

      <div className="header-controls">
        <button
          type="button"
          className="area-chip"
          onClick={() => update({ tab: "explore" })}
          title="Change the area of interest on the Explore tab"
        >
          <span className="area-chip-label">Area</span>
          <span className="area-chip-name">{aoi.info?.name ?? (aoi.isLoading ? "Loading" : "Choose an area")}</span>
          {aoi.info && <span className="area-chip-size">{formatArea(aoi.info.area_km2)}</span>}
        </button>
        <Segmented<Metric>
          label="Metric"
          value={state.metric}
          onChange={(metric) => update({ metric })}
          options={[
            { value: "count", label: "Detections", title: "Number of active fire detections" },
            { value: "frp", label: "Fire power", title: "Fire radiative power in megawatts" },
          ]}
        />
        <label className="select-field">
          <span className="control-label">Confidence</span>
          <select
            value={state.confidence}
            onChange={(event) => update({ confidence: event.target.value as ConfidenceFilter })}
          >
            {(meta.data?.confidence_filters ?? [{ key: "nominal" as const, label: "Nominal and high confidence" }]).map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="btn" onClick={() => void share()} aria-live="polite">
          {copied === "done" ? "Link copied" : copied === "failed" ? "Copy failed" : "Copy link"}
        </button>
      </div>

      <div className="freshness" role="status">
        {coverage?.ready ? (
          <>
            <span className={`dot ${referenceSilent ? "dot-warn" : "dot-ok"}`} aria-hidden="true" />
            <span>
              Data through {formatDate(coverage.last_complete_day)}
              {referenceSilent && snpp?.last_observed
                ? `. VIIRS S-NPP silent since ${formatDate(snpp.last_observed)}; NOAA-20 is carrying the record`
                : ""}
            </span>
          </>
        ) : meta.isLoading ? (
          <span>Connecting to the server</span>
        ) : (
          <>
            <span className="dot dot-danger" aria-hidden="true" />
            <span>No data ingested yet</span>
          </>
        )}
      </div>
    </header>
  );
}

export function TabBar() {
  const { state, update } = useAppState();
  const onKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    const moves: Record<string, number> = {
      ArrowRight: (index + 1) % TABS.length,
      ArrowLeft: (index - 1 + TABS.length) % TABS.length,
      Home: 0,
      End: TABS.length - 1,
    };
    const next = moves[event.key];
    if (next === undefined) return;
    event.preventDefault();
    const target = TABS[next];
    if (!target) return;
    update({ tab: target.key });
    document.getElementById(`tab-${target.key}`)?.focus();
  };
  return (
    <nav className="tabbar" aria-label="Sections">
      <div role="tablist" aria-label="AgniSetu sections" className="tablist">
        {TABS.map((tab, index) => (
          <button
            key={tab.key}
            id={`tab-${tab.key}`}
            role="tab"
            type="button"
            aria-selected={state.tab === tab.key}
            aria-controls={`panel-${tab.key}`}
            tabIndex={state.tab === tab.key ? 0 : -1}
            className={`tab ${state.tab === tab.key ? "is-active" : ""}`}
            onClick={() => update({ tab: tab.key as TabKey })}
            onKeyDown={(event) => onKeyDown(event, index)}
          >
            {tab.label}
          </button>
        ))}
      </div>
    </nav>
  );
}
