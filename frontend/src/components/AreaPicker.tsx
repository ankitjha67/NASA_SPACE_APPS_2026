import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { api } from "../api/client";
import { useAoi, useMeta, usePresetSearch } from "../api/hooks";
import type { AreaGeometry, PresetEntry } from "../api/types";
import { boxName, formatArea, formatPercent } from "../lib/format";
import { boxGeometry, circleGeometry, GeoJsonError, MAX_UPLOAD_BYTES, parseGeoJsonText } from "../lib/geo";
import { storeArea, useAppState } from "../state/AppState";
import type { DrawMode } from "./MapView";
import { describeError, Notice, Segmented, Spinner } from "./ui";

const LEVEL_LABELS: Record<PresetEntry["level"], string> = {
  region: "Region",
  country: "Country",
  state: "State or province",
  district: "District",
};

function useDebounced<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export function AreaPicker({
  drawMode,
  onDrawModeChange,
  pendingGeometry,
  onPendingHandled,
}: {
  drawMode: DrawMode;
  onDrawModeChange: (mode: DrawMode) => void;
  /** A geometry just drawn on the map, waiting to be validated by the server. */
  pendingGeometry: AreaGeometry | null;
  onPendingHandled: () => void;
}) {
  const { state, update } = useAppState();
  const meta = useMeta();
  const aoi = useAoi();
  const [query, setQuery] = useState("");
  const debounced = useDebounced(query.trim(), 200);
  const search = usePresetSearch(debounced);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const counter = useRef(1);
  const [coordOpen, setCoordOpen] = useState(false);
  const [coordMode, setCoordMode] = useState<"point" | "box">("point");
  const [point, setPoint] = useState({ lat: "", lon: "", radius: "25" });
  const [boxFields, setBoxFields] = useState({ west: "", south: "", east: "", north: "" });

  const submitGeometry = async (geometry: AreaGeometry, name: string) => {
    setBusy(true);
    setProblem(null);
    try {
      const validated = await api.validateAoi(geometry, name);
      const key = storeArea(validated.geometry, validated.aoi.name);
      update({ customKey: key, presetId: null });
    } catch (error) {
      setProblem(describeError(error).detail);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!pendingGeometry) return;
    const name = `Drawn area ${counter.current}`;
    counter.current += 1;
    onPendingHandled();
    void submitGeometry(pendingGeometry, name);
    // submitGeometry is stable for our purposes; this effect reacts only to a new drawn geometry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingGeometry]);

  const onFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setProblem(null);
    if (file.size > MAX_UPLOAD_BYTES) {
      setProblem(`The file is larger than ${Math.round(MAX_UPLOAD_BYTES / 1024)} KB. Simplify the geometry and try again.`);
      return;
    }
    try {
      const geometry = parseGeoJsonText(await file.text());
      const name = file.name.replace(/\.(geo)?json$/i, "").slice(0, 80) || "Uploaded area";
      await submitGeometry(geometry, name);
    } catch (error) {
      setProblem(error instanceof GeoJsonError ? error.message : "The file could not be read.");
    }
  };

  const onCoordinates = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setProblem(null);
    const number = (text: string) => (text.trim() === "" ? Number.NaN : Number(text));
    try {
      if (coordMode === "point") {
        const lat = number(point.lat);
        const lon = number(point.lon);
        const radius = number(point.radius);
        if (![lat, lon, radius].every(Number.isFinite)) throw new GeoJsonError("Enter a latitude, a longitude and a radius.");
        const geometry = circleGeometry(lat, lon, radius);
        await submitGeometry(geometry, `Within ${radius} km of ${lat.toFixed(3)}, ${lon.toFixed(3)}`);
      } else {
        const west = number(boxFields.west);
        const south = number(boxFields.south);
        const east = number(boxFields.east);
        const north = number(boxFields.north);
        const geometry = boxGeometry(west, south, east, north);
        await submitGeometry(geometry, boxName(west, south, east, north));
      }
    } catch (error) {
      setProblem(error instanceof GeoJsonError ? error.message : "These coordinates could not be used.");
    }
  };

  const items = search.data?.items ?? [];
  const info = aoi.info;
  const limits = meta.data?.limits;

  return (
    <div className="area-picker">
      <label className="control-label" htmlFor="area-search">
        Find a region, state, province or district
      </label>
      <div className="search-row">
        <input
          id="area-search"
          type="search"
          value={query}
          placeholder="For example Sangrur, Punjab, La Rioja"
          autoComplete="off"
          maxLength={60}
          onChange={(event) => setQuery(event.target.value)}
        />
        {search.isFetching && <Spinner label="Searching" />}
      </div>
      <ul className="preset-list" aria-label="Matching areas">
        {items.map((item) => {
          const active = state.presetId === item.id || (!state.presetId && !state.customKey && meta.data?.default_preset === item.id);
          return (
            <li key={item.id}>
              <button
                type="button"
                className={`preset-item ${active ? "is-active" : ""}`}
                aria-pressed={active}
                onClick={() => update({ presetId: item.id, customKey: null })}
                title={item.description ?? undefined}
              >
                <span className="preset-name">{item.name}</span>
                <span className="preset-meta">
                  {LEVEL_LABELS[item.level]}
                  {item.parent ? `, ${item.parent}` : item.country && item.level !== "country" ? `, ${item.country}` : ""}
                </span>
              </button>
            </li>
          );
        })}
        {search.isError && <li className="muted small">Search is unavailable: {describeError(search.error).detail}</li>}
        {!search.isError && !search.isFetching && items.length === 0 && (
          <li className="muted small">No area matches "{debounced}". Try a state, province or district name, or draw an area.</li>
        )}
      </ul>

      <div className="draw-row" role="group" aria-label="Define a custom area">
        <button
          type="button"
          className={`btn ${drawMode === "rectangle" ? "is-on" : ""}`}
          aria-pressed={drawMode === "rectangle"}
          onClick={() => onDrawModeChange(drawMode === "rectangle" ? "none" : "rectangle")}
        >
          Draw rectangle
        </button>
        <button
          type="button"
          className={`btn ${drawMode === "polygon" ? "is-on" : ""}`}
          aria-pressed={drawMode === "polygon"}
          onClick={() => onDrawModeChange(drawMode === "polygon" ? "none" : "polygon")}
        >
          Draw polygon
        </button>
        <button type="button" className="btn" onClick={() => fileInput.current?.click()} disabled={busy}>
          Upload GeoJSON
        </button>
        <button
          type="button"
          className={`btn ${coordOpen ? "is-on" : ""}`}
          aria-expanded={coordOpen}
          aria-controls="coordinate-form"
          onClick={() => setCoordOpen((open) => !open)}
        >
          Enter coordinates
        </button>
        <input
          ref={fileInput}
          type="file"
          accept=".json,.geojson,application/geo+json,application/json"
          className="sr-only"
          aria-label="Upload a GeoJSON file with a polygon"
          onChange={(event) => void onFile(event)}
          tabIndex={-1}
        />
      </div>
      {coordOpen && (
        <form id="coordinate-form" className="coord-form" onSubmit={(event) => void onCoordinates(event)} aria-label="Define an area by coordinates">
          <Segmented
            label="Coordinate entry"
            hideLabel
            value={coordMode}
            onChange={setCoordMode}
            options={[
              { value: "point", label: "Point and radius" },
              { value: "box", label: "Bounding box" },
            ]}
          />
          {coordMode === "point" ? (
            <div className="coord-grid">
              <label>
                <span className="control-label">Latitude</span>
                <input type="number" step="any" min={-89.5} max={89.5} required placeholder="30.25" value={point.lat} onChange={(e) => setPoint({ ...point, lat: e.target.value })} />
              </label>
              <label>
                <span className="control-label">Longitude</span>
                <input type="number" step="any" min={-180} max={180} required placeholder="75.84" value={point.lon} onChange={(e) => setPoint({ ...point, lon: e.target.value })} />
              </label>
              <label>
                <span className="control-label">Radius, km</span>
                <input type="number" step="any" min={1} max={1000} required value={point.radius} onChange={(e) => setPoint({ ...point, radius: e.target.value })} />
              </label>
            </div>
          ) : (
            <div className="coord-grid">
              <label>
                <span className="control-label">West longitude</span>
                <input type="number" step="any" min={-180} max={180} required placeholder="75.0" value={boxFields.west} onChange={(e) => setBoxFields({ ...boxFields, west: e.target.value })} />
              </label>
              <label>
                <span className="control-label">East longitude</span>
                <input type="number" step="any" min={-180} max={180} required placeholder="76.5" value={boxFields.east} onChange={(e) => setBoxFields({ ...boxFields, east: e.target.value })} />
              </label>
              <label>
                <span className="control-label">South latitude</span>
                <input type="number" step="any" min={-90} max={90} required placeholder="29.5" value={boxFields.south} onChange={(e) => setBoxFields({ ...boxFields, south: e.target.value })} />
              </label>
              <label>
                <span className="control-label">North latitude</span>
                <input type="number" step="any" min={-90} max={90} required placeholder="31.0" value={boxFields.north} onChange={(e) => setBoxFields({ ...boxFields, north: e.target.value })} />
              </label>
            </div>
          )}
          <button type="submit" className="btn" disabled={busy}>
            Use this area
          </button>
          <p className="muted small">Decimal degrees, WGS84. North and east are positive.</p>
        </form>
      )}
      {limits && (
        <p className="muted small">
          Custom areas: {formatArea(limits.min_aoi_area_km2)} to {formatArea(limits.max_aoi_area_km2)}, up to{" "}
          {limits.max_aoi_vertices.toLocaleString("en-IN")} vertices.
        </p>
      )}
      {busy && (
        <p className="small" role="status">
          <Spinner label="Validating the area" /> Validating the area
        </p>
      )}
      {problem && <Notice tone="danger">{problem}</Notice>}
      {aoi.missingCustom && (
        <Notice tone="warn">
          This link points to a custom area that is stored only in the browser where it was drawn. Choose or draw an
          area to continue.
        </Notice>
      )}
      {aoi.error && !aoi.missingCustom && <Notice tone="danger">{describeError(aoi.error).detail}</Notice>}

      {info && (
        <dl className="area-facts">
          <div>
            <dt>Selected</dt>
            <dd>{info.name}</dd>
          </div>
          <div>
            <dt>Size</dt>
            <dd>{formatArea(info.area_km2)}</dd>
          </div>
          <div>
            <dt>Shape</dt>
            <dd>
              {info.is_rectangle ? "Rectangle" : `${info.vertices.toLocaleString("en-IN")} vertices`}
              {info.repaired ? " (repaired)" : ""}
            </dd>
          </div>
          <div>
            <dt>Inside data domain</dt>
            <dd>{formatPercent(info.domain_fraction, 1)}</dd>
          </div>
        </dl>
      )}
      {info && info.domain_fraction < 0.99 && (
        <Notice tone="warn">
          Only {formatPercent(info.domain_fraction, 1)} of this area lies inside the ingested countries (
          {info.domain_countries.join(", ")}). Results describe that part only.
        </Notice>
      )}
      {info?.repaired && (
        <Notice tone="info">The shape had self intersections or other defects and was repaired before use.</Notice>
      )}
    </div>
  );
}
