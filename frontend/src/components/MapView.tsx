import "maplibre-gl/dist/maplibre-gl.css";
import {
  Map as MapLibreMap,
  Marker,
  NavigationControl,
  Popup,
  ScaleControl,
  setWorkerUrl,
  type GeoJSONSource,
  type MapMouseEvent,
  type RasterTileSource,
  type StyleSpecification,
} from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type { Feature, FeatureCollection, Geometry, Polygon } from "geojson";
import { useEffect, useRef, useState } from "react";
import type { AreaGeometry, Position } from "../api/types";
import { THEME } from "../lib/colors";
import { saveCanvas } from "../lib/download";
import { polygonGeometry, rectangleGeometry } from "../lib/geo";
import type { Basemap } from "../state/urlState";

// MapLibre 6 ships its worker as a separate module. Point it at the copy the bundler emits so it
// is served from this origin, which the Content Security Policy allows.
setWorkerUrl(workerUrl);

const GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best";
const NASA_ATTRIBUTION = "Imagery: NASA GIBS / ESDIS";
const OSM_ATTRIBUTION = "© OpenStreetMap contributors";
const LABEL_MIN_ZOOM = 4;
const SMALL_STATE_MIN_ZOOM = 6.5;
const SMALL_STATE_KM2 = 12_000;

export interface ReferenceLayer {
  type: "FeatureCollection";
  features: {
    type: "Feature";
    properties: { id: string; name: string; country: string; label_lon: number; label_lat: number; area_km2: number };
    geometry: AreaGeometry;
  }[];
}

export type DrawMode = "none" | "rectangle" | "polygon";

export interface MapFeatureCollection {
  type: "FeatureCollection";
  features: {
    type: "Feature";
    geometry: { type: "Point"; coordinates: Position } | { type: "Polygon"; coordinates: Position[][] };
    properties: Record<string, string | number | null>;
  }[];
}

export interface PopupRow {
  label: string;
  value: string;
}

interface MapViewProps {
  basemap: Basemap;
  imageryDate: string | null;
  aoi: AreaGeometry | null;
  /** The map fits to the area whenever this key changes. */
  fitKey: string | null;
  fitBounds: [number, number, number, number] | null;
  points?: MapFeatureCollection | null;
  cells?: MapFeatureCollection | null;
  /** State outlines and names drawn over satellite imagery for orientation. */
  reference?: ReferenceLayer | null;
  drawMode?: DrawMode;
  onDrawComplete?: (geometry: AreaGeometry) => void;
  onDrawCancel?: () => void;
  onViewChange?: (bbox: [number, number, number, number]) => void;
  describeFeature?: (properties: Record<string, unknown>, layer: "points" | "cells") => { title: string; rows: PopupRow[] } | null;
  ariaLabel: string;
  height?: number | string;
  /** When set, a "Save image" button exports the current map view as a PNG with this file name stem. */
  downloadName?: string;
}

const EMPTY: MapFeatureCollection = { type: "FeatureCollection", features: [] };

function baseTiles(basemap: Basemap, date: string | null): { tiles: string[]; maxzoom: number; attribution: string } {
  if (basemap === "streets") {
    return { tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], maxzoom: 19, attribution: OSM_ATTRIBUTION };
  }
  if (basemap === "truecolor" && date) {
    const layer = date >= "2018-01-05" ? "VIIRS_NOAA20_CorrectedReflectance_TrueColor" : "MODIS_Terra_CorrectedReflectance_TrueColor";
    return {
      tiles: [`${GIBS}/${layer}/default/${date}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`],
      maxzoom: 9,
      attribution: NASA_ATTRIBUTION,
    };
  }
  return {
    tiles: [`${GIBS}/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg`],
    maxzoom: 8,
    attribution: NASA_ATTRIBUTION,
  };
}

function buildStyle(basemap: Basemap, date: string | null): StyleSpecification {
  const base = baseTiles(basemap, date);
  return {
    version: 8,
    sources: {
      base: { type: "raster", tiles: base.tiles, tileSize: 256, maxzoom: base.maxzoom, attribution: base.attribution },
      reference: { type: "geojson", data: EMPTY },
      aoi: { type: "geojson", data: EMPTY },
      cells: { type: "geojson", data: EMPTY },
      points: { type: "geojson", data: EMPTY },
      draw: { type: "geojson", data: EMPTY },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": "#0a0d12" } },
      { id: "base", type: "raster", source: "base", paint: { "raster-fade-duration": 150 } },
      {
        id: "reference-line",
        type: "line",
        source: "reference",
        paint: { "line-color": "#ffffff", "line-opacity": 0.38, "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.5, 8, 1.1] },
      },
      { id: "cells-fill", type: "fill", source: "cells", paint: { "fill-color": ["get", "color"], "fill-opacity": 0.78 } },
      { id: "aoi-fill", type: "fill", source: "aoi", paint: { "fill-color": THEME.cool, "fill-opacity": 0.06 } },
      { id: "aoi-line", type: "line", source: "aoi", paint: { "line-color": "#ffffff", "line-width": 1.8, "line-opacity": 0.95 } },
      {
        id: "points-circle",
        type: "circle",
        source: "points",
        paint: {
          "circle-color": ["get", "color"],
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 1.6, 6, 2.6, 9, 4.5, 12, 7],
          "circle-opacity": 0.85,
          "circle-stroke-color": "rgba(0,0,0,0.55)",
          "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 5, 0, 8, 0.6],
        },
      },
      { id: "draw-fill", type: "fill", source: "draw", filter: ["==", ["geometry-type"], "Polygon"], paint: { "fill-color": THEME.fire, "fill-opacity": 0.15 } },
      { id: "draw-line", type: "line", source: "draw", paint: { "line-color": THEME.fire, "line-width": 2, "line-dasharray": [2, 1.5] } },
      {
        id: "draw-vertex",
        type: "circle",
        source: "draw",
        filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": 4, "circle-color": "#ffffff", "circle-stroke-color": THEME.fire, "circle-stroke-width": 2 },
      },
    ],
  };
}

function drawData(vertices: Position[], cursor: Position | null, mode: DrawMode): FeatureCollection {
  const features: Feature[] = [];
  if (mode === "rectangle" && vertices[0] && cursor) {
    features.push({ type: "Feature", properties: {}, geometry: rectangleGeometry(vertices[0], cursor) as Polygon });
  }
  if (mode === "polygon") {
    const path = cursor ? [...vertices, cursor] : vertices;
    const first = path[0];
    if (path.length >= 3 && first) {
      features.push({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [[...path, first]] } });
    } else if (path.length === 2) {
      features.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: path } });
    }
    for (const v of vertices) {
      features.push({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: v } });
    }
  }
  return { type: "FeatureCollection", features };
}

export function MapView({
  basemap,
  imageryDate,
  aoi,
  fitKey,
  fitBounds,
  points,
  cells,
  reference,
  drawMode = "none",
  onDrawComplete,
  onDrawCancel,
  onViewChange,
  describeFeature,
  ariaLabel,
  height = 520,
  downloadName,
}: MapViewProps) {
  const host = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [saveFailed, setSaveFailed] = useState(false);
  const callbacks = useRef({ onDrawComplete, onDrawCancel, onViewChange, describeFeature });
  const drawState = useRef<{ mode: DrawMode; vertices: Position[]; dragging: boolean }>({ mode: "none", vertices: [], dragging: false });
  const initial = useRef({ basemap, imageryDate });

  useEffect(() => {
    callbacks.current = { onDrawComplete, onDrawCancel, onViewChange, describeFeature };
  }, [onDrawComplete, onDrawCancel, onViewChange, describeFeature]);

  // Create the map once.
  useEffect(() => {
    const element = host.current;
    if (!element) return undefined;
    let map: MapLibreMap;
    try {
      map = new MapLibreMap({
        container: element,
        style: buildStyle(initial.current.basemap, initial.current.imageryDate),
        center: [78.5, 23],
        zoom: 3.6,
        minZoom: 2,
        maxZoom: 14,
        attributionControl: { compact: true },
        dragRotate: false,
        // Keep the last frame readable so the view can be saved as an image.
        canvasContextAttributes: { preserveDrawingBuffer: true },
        pitchWithRotate: false,
        touchPitch: false,
      });
    } catch (cause) {
      // Report after the effect returns; the map is an external system that failed to start.
      const message = cause instanceof Error ? cause.message : "WebGL is not available in this browser.";
      const timer = window.setTimeout(() => setFailed(message), 0);
      return () => window.clearTimeout(timer);
    }
    mapRef.current = map;
    map.touchZoomRotate.disableRotation();
    map.addControl(new NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new ScaleControl({ unit: "metric" }), "bottom-left");

    let moveTimer: number | undefined;
    const emitView = () => {
      window.clearTimeout(moveTimer);
      moveTimer = window.setTimeout(() => {
        const b = map.getBounds();
        callbacks.current.onViewChange?.([
          Math.max(-180, b.getWest()),
          Math.max(-90, b.getSouth()),
          Math.min(180, b.getEast()),
          Math.min(90, b.getNorth()),
        ]);
      }, 250);
    };
    map.on("moveend", emitView);
    map.on("load", () => {
      setReady(true);
      emitView();
    });

    const popup = new Popup({ closeButton: true, maxWidth: "280px", className: "map-popup" });
    const showPopup = (event: MapMouseEvent, layer: "points" | "cells") => {
      if (drawState.current.mode !== "none") return;
      const hits = map.queryRenderedFeatures(event.point, { layers: [layer === "points" ? "points-circle" : "cells-fill"] });
      const feature = hits[0];
      if (!feature) return;
      const content = callbacks.current.describeFeature?.(feature.properties ?? {}, layer);
      if (!content) return;
      const box = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = content.title;
      box.appendChild(title);
      const list = document.createElement("dl");
      for (const row of content.rows) {
        const dt = document.createElement("dt");
        dt.textContent = row.label;
        const dd = document.createElement("dd");
        dd.textContent = row.value;
        list.append(dt, dd);
      }
      box.appendChild(list);
      popup.setLngLat(event.lngLat).setDOMContent(box).addTo(map);
    };
    map.on("click", "points-circle", (event) => showPopup(event, "points"));
    map.on("click", "cells-fill", (event) => {
      const onPoint = map.queryRenderedFeatures(event.point, { layers: ["points-circle"] }).length > 0;
      if (!onPoint) showPopup(event, "cells");
    });
    for (const layer of ["points-circle", "cells-fill"]) {
      map.on("mouseenter", layer, () => {
        if (drawState.current.mode === "none") map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", layer, () => {
        if (drawState.current.mode === "none") map.getCanvas().style.cursor = "";
      });
    }

    // Drawing.
    const setDraw = (cursor: Position | null) => {
      const source = map.getSource("draw") as GeoJSONSource | undefined;
      source?.setData(drawData(drawState.current.vertices, cursor, drawState.current.mode));
    };
    const position = (event: MapMouseEvent): Position => [
      Number(event.lngLat.lng.toFixed(5)),
      Number(event.lngLat.lat.toFixed(5)),
    ];
    const finishPolygon = () => {
      const state = drawState.current;
      if (state.mode !== "polygon" || state.vertices.length < 3) return;
      const geometry = polygonGeometry(state.vertices);
      state.vertices = [];
      setDraw(null);
      callbacks.current.onDrawComplete?.(geometry);
    };
    map.on("mousedown", (event) => {
      const state = drawState.current;
      if (state.mode !== "rectangle") return;
      event.preventDefault();
      state.vertices = [position(event)];
      state.dragging = true;
    });
    map.on("mousemove", (event) => {
      const state = drawState.current;
      if (state.mode === "rectangle" && state.dragging) setDraw(position(event));
      if (state.mode === "polygon" && state.vertices.length > 0) setDraw(position(event));
    });
    map.on("mouseup", (event) => {
      const state = drawState.current;
      if (state.mode !== "rectangle" || !state.dragging) return;
      state.dragging = false;
      const start = state.vertices[0];
      const end = position(event);
      state.vertices = [];
      setDraw(null);
      if (start && Math.abs(start[0] - end[0]) > 1e-4 && Math.abs(start[1] - end[1]) > 1e-4) {
        callbacks.current.onDrawComplete?.(rectangleGeometry(start, end));
      }
    });
    map.on("click", (event) => {
      const state = drawState.current;
      if (state.mode !== "polygon") return;
      state.vertices = [...state.vertices, position(event)];
      setDraw(null);
    });
    map.on("dblclick", (event) => {
      if (drawState.current.mode !== "polygon") return;
      event.preventDefault();
      // The two clicks of a double click each added a vertex at the same place; drop one.
      drawState.current.vertices = drawState.current.vertices.slice(0, -1);
      finishPolygon();
    });
    const onKey = (event: KeyboardEvent) => {
      const state = drawState.current;
      if (state.mode === "none") return;
      if (event.key === "Escape") {
        state.vertices = [];
        state.dragging = false;
        setDraw(null);
        callbacks.current.onDrawCancel?.();
      } else if (event.key === "Enter" && state.mode === "polygon") {
        finishPolygon();
      } else if (event.key === "Backspace" && state.mode === "polygon") {
        state.vertices = state.vertices.slice(0, -1);
        setDraw(null);
      }
    };
    window.addEventListener("keydown", onKey);

    const observer = new ResizeObserver(() => map.resize());
    observer.observe(element);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.clearTimeout(moveTimer);
      observer.disconnect();
      popup.remove();
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Basemap and imagery date.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const base = baseTiles(basemap, imageryDate);
    const source = map.getSource("base") as RasterTileSource | undefined;
    source?.setTiles(base.tiles);
    // The street map already carries borders and names.
    map.setLayoutProperty("reference-line", "visibility", basemap === "streets" ? "none" : "visible");
    host.current?.classList.toggle("hide-map-labels", basemap === "streets");
  }, [basemap, imageryDate, ready]);

  // State outlines and name labels. Labels are DOM markers, so no font server is needed.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return undefined;
    (map.getSource("reference") as GeoJSONSource | undefined)?.setData(
      (reference ?? EMPTY) as unknown as FeatureCollection,
    );
    if (!reference) return undefined;
    const markers = reference.features.map((feature) => {
      const element = document.createElement("div");
      element.className = "map-label";
      element.textContent = feature.properties.name;
      element.dataset.minZoom = String(feature.properties.area_km2 < SMALL_STATE_KM2 ? SMALL_STATE_MIN_ZOOM : LABEL_MIN_ZOOM);
      return new Marker({ element, anchor: "center" })
        .setLngLat([feature.properties.label_lon, feature.properties.label_lat])
        .addTo(map);
    });
    // Larger states win. A label is hidden when it would touch one that is already shown.
    const order = reference.features
      .map((feature, index) => ({ index, area: feature.properties.area_km2 }))
      .sort((a, b) => b.area - a.area)
      .map((item) => item.index);
    const applyLabels = () => {
      const zoom = map.getZoom();
      const frame = map.getContainer().getBoundingClientRect();
      // Map controls (zoom, attribution) count as taken space, so a name never sits under a button.
      const shown: DOMRect[] = Array.from(map.getContainer().querySelectorAll(".maplibregl-ctrl"))
        .map((control) => control.getBoundingClientRect())
        .filter((box) => box.width > 0 && box.height > 0);
      for (const index of order) {
        const element = markers[index]?.getElement();
        if (!element) continue;
        let visible = zoom >= Number(element.dataset.minZoom);
        if (visible) {
          const box = element.getBoundingClientRect();
          const inside = box.left >= frame.left + 4 && box.right <= frame.right - 4 && box.top >= frame.top + 4 && box.bottom <= frame.bottom - 4;
          const pad = 4;
          visible =
            inside &&
            !shown.some((o) => box.left < o.right + pad && box.right + pad > o.left && box.top < o.bottom + pad && box.bottom + pad > o.top);
          if (visible) shown.push(box);
        }
        element.style.visibility = visible ? "visible" : "hidden";
      }
    };
    // Markers get their screen position on the next frame, so the first pass waits for it.
    const firstPass = requestAnimationFrame(applyLabels);
    map.on("move", applyLabels);
    map.on("idle", applyLabels);
    return () => {
      cancelAnimationFrame(firstPass);
      map.off("move", applyLabels);
      map.off("idle", applyLabels);
      for (const marker of markers) marker.remove();
    };
  }, [reference, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const source = map.getSource("aoi") as GeoJSONSource | undefined;
    source?.setData(
      aoi ? { type: "Feature", properties: {}, geometry: aoi as Geometry } : (EMPTY as FeatureCollection),
    );
  }, [aoi, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !fitBounds || !fitKey) return;
    const [west, south, east, north] = fitBounds;
    map.fitBounds([[west, south], [east, north]], { padding: 36, duration: 500, maxZoom: 11 });
    // Fit only when the area identity changes, not on every bounds array identity change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    (map.getSource("points") as GeoJSONSource | undefined)?.setData((points ?? EMPTY) as FeatureCollection);
  }, [points, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    (map.getSource("cells") as GeoJSONSource | undefined)?.setData((cells ?? EMPTY) as FeatureCollection);
  }, [cells, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    drawState.current = { mode: drawMode, vertices: [], dragging: false };
    (map.getSource("draw") as GeoJSONSource | undefined)?.setData(EMPTY as FeatureCollection);
    if (drawMode === "none") {
      map.dragPan.enable();
      map.doubleClickZoom.enable();
      map.getCanvas().style.cursor = "";
    } else {
      if (drawMode === "rectangle") map.dragPan.disable();
      else map.dragPan.enable();
      map.doubleClickZoom.disable();
      map.getCanvas().style.cursor = "crosshair";
    }
  }, [drawMode, ready]);

  if (failed) {
    return (
      <div className="state-block is-error" style={{ height }} role="alert">
        <strong>The map could not start</strong>
        <span>{failed}</span>
        <span className="muted small">Charts and tables on the other tabs do not need the map.</span>
      </div>
    );
  }
  const save = async () => {
    const map = mapRef.current;
    if (!map || !downloadName) return;
    const saved = await saveCanvas(map.getCanvas(), `${downloadName}.png`, "#0a0d12");
    if (!saved) setSaveFailed(true);
  };

  return (
    <div className="map-wrap" style={{ height }}>
      <div ref={host} className="map" role="application" aria-label={ariaLabel} />
      {downloadName && ready && (
        <button type="button" className="btn btn-mini map-save" onClick={() => void save()} aria-label={`Save image: ${ariaLabel}`}>
          {saveFailed ? "Image not available" : "Save image"}
        </button>
      )}
      {drawMode !== "none" && (
        <div className="map-hint" role="status">
          {drawMode === "rectangle"
            ? "Drag on the map to draw a rectangle. Press Escape to cancel."
            : "Click to add points. Double click or press Enter to finish, Backspace to undo, Escape to cancel."}
        </div>
      )}
    </div>
  );
}
