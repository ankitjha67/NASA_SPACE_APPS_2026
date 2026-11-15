import { describe, expect, it } from "vitest";
import { fireColor, ratioColor, VALUE_TYPE_BY_CODE } from "../lib/colors";
import {
  addDays,
  clampDate,
  doyLabel,
  formatAmount,
  formatBytes,
  formatDate,
  formatNumber,
  formatPercent,
  formatRatio,
  formatSignedPercent,
  localSolarTime,
  monthTicks,
  ordinal,
  utcTime,
} from "../lib/format";
import { cellPolygon, GeoJsonError, geometryKey, parseGeoJsonText, polygonGeometry, rectangleGeometry } from "../lib/geo";
import { decodeState, DEFAULT_STATE, encodeState, type AppState } from "../state/urlState";

describe("format", () => {
  it("renders missing values as n/a instead of zero", () => {
    expect(formatNumber(null)).toBe("n/a");
    expect(formatAmount(undefined)).toBe("n/a");
    expect(formatAmount(Number.NaN)).toBe("n/a");
    expect(formatPercent(null)).toBe("n/a");
    expect(formatRatio(null)).toBe("n/a");
    expect(formatDate(null)).toBe("n/a");
  });

  it("formats amounts with one decimal when small and whole numbers when large", () => {
    expect(formatAmount(54)).toBe("54.0");
    expect(formatAmount(26879)).toBe("26,879");
    expect(formatPercent(0.215, 1)).toBe("21.5%");
    expect(formatSignedPercent(0.031, 1)).toBe("+3.1%");
    expect(formatSignedPercent(-0.777, 1)).toBe("-77.7%");
    expect(formatRatio(4.956)).toBe("5.0x");
    expect(formatRatio(0.25)).toBe("0.25x");
    expect(formatBytes(1274591555)).toBe("1.27 GB");
  });

  it("writes ordinals correctly including the teens", () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 92, 100].map(ordinal)).toEqual([
      "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "92nd", "100th",
    ]);
  });

  it("handles calendar dates without time zone drift", () => {
    expect(formatDate("2016-11-05")).toBe("Nov 5, 2016");
    expect(formatDate("2016-11-05", "long")).toBe("November 5, 2016");
    expect(addDays("2024-02-28", 2)).toBe("2024-03-01");
    expect(addDays("2025-01-01", -1)).toBe("2024-12-31");
    expect(clampDate("1999-01-01", "2000-11-01", "2026-11-14")).toBe("2000-11-01");
    expect(clampDate("2030-01-01", "2000-11-01", "2026-11-14")).toBe("2026-11-14");
    expect(doyLabel(1)).toBe("Jan 1");
    expect(doyLabel(365)).toBe("Dec 31");
  });

  it("places month ticks for weekly and daily grids", () => {
    const weekly = monthTicks(52);
    expect(weekly).toHaveLength(12);
    expect(weekly[0]).toEqual({ index: 0, label: "Jan" });
    expect(weekly[10]).toEqual({ index: 43, label: "Nov" });
    expect(monthTicks(366)[1]).toEqual({ index: 31, label: "Feb" });
  });

  it("converts acquisition time to local solar time", () => {
    expect(utcTime(745)).toBe("07:45");
    expect(localSolarTime(745, 76)).toBe("12:49");
    expect(localSolarTime(2030, 76)).toBe("01:34");
  });
});

describe("colors", () => {
  it("maps activity monotonically from dark to bright", () => {
    const brightness = (rgb: string) => rgb.match(/\d+/g)!.map(Number).reduce((a, b) => a + b, 0);
    const steps = [0, 10, 100, 1000, 5000, 10000].map((v) => brightness(fireColor(v, 10000)));
    for (let i = 1; i < steps.length; i += 1) expect(steps[i]!).toBeGreaterThan(steps[i - 1]!);
    expect(fireColor(50000, 10000)).toBe(fireColor(10000, 10000));
    expect(fireColor(5, 0)).toBe(fireColor(0, 10));
  });

  it("clamps the ratio ramp and keeps value type codes aligned with the API", () => {
    expect(ratioColor(1, 3, 6)).toBe(ratioColor(3, 3, 6));
    expect(ratioColor(9, 3, 6)).toBe(ratioColor(6, 3, 6));
    expect(VALUE_TYPE_BY_CODE).toEqual(["missing", "reference", "viirs_calibrated", "modis_harmonized"]);
  });
});

describe("geo", () => {
  it("extracts polygons from any GeoJSON container", () => {
    const ring = [[75, 30], [76, 30], [76, 31], [75, 31], [75, 30]];
    const feature = JSON.stringify({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring] } });
    expect(parseGeoJsonText(feature)).toEqual({ type: "Polygon", coordinates: [ring] });
    const collection = JSON.stringify({
      type: "FeatureCollection",
      features: [
        { type: "Feature", geometry: { type: "Point", coordinates: [1, 2] } },
        { type: "Feature", geometry: { type: "Polygon", coordinates: [ring] } },
        { type: "Feature", geometry: { type: "MultiPolygon", coordinates: [[ring]] } },
      ],
    });
    const parsed = parseGeoJsonText(collection);
    expect(parsed.type).toBe("MultiPolygon");
    expect(parsed.coordinates).toHaveLength(2);
  });

  it("rejects text that is not usable", () => {
    expect(() => parseGeoJsonText("{nope")).toThrow(GeoJsonError);
    expect(() => parseGeoJsonText('{"type":"Point","coordinates":[1,2]}')).toThrow(/No Polygon/);
    expect(() => parseGeoJsonText('{"type":"Polygon","coordinates":[[[1,2],[3,4],[1,2]]]}')).toThrow(/four positions/);
    expect(() => parseGeoJsonText('{"type":"Polygon","coordinates":[[[500,2],[3,4],[5,6],[500,2]]]}')).toThrow(/WGS84/);
    expect(() => parseGeoJsonText('{"type":"Polygon","coordinates":[[["a",2],[3,4],[5,6],["a",2]]]}')).toThrow(/number pairs/);
  });

  it("builds closed rings for drawn shapes", () => {
    expect(rectangleGeometry([76, 31], [75, 30]).coordinates[0]).toEqual([[75, 30], [76, 30], [76, 31], [75, 31], [75, 30]]);
    const polygon = polygonGeometry([[0, 0], [1, 0], [1, 1]]);
    expect(polygon.coordinates[0]).toHaveLength(4);
    expect(() => polygonGeometry([[0, 0], [1, 1]])).toThrow(GeoJsonError);
    expect(cellPolygon(75, 30, 0.5)[0]).toHaveLength(5);
  });

  it("gives equal geometries the same key and different ones different keys", () => {
    const a = rectangleGeometry([75, 30], [76, 31]);
    expect(geometryKey(a)).toBe(geometryKey(rectangleGeometry([76, 31], [75, 30])));
    expect(geometryKey(a)).not.toBe(geometryKey(rectangleGeometry([75, 30], [76, 31.5])));
    expect(geometryKey(a)).toMatch(/^[a-z0-9]{4,32}$/);
  });
});

describe("url state", () => {
  it("round trips every field", () => {
    const state: AppState = {
      tab: "warning",
      presetId: "adm2:IND:punjab:sangrur",
      customKey: null,
      metric: "frp",
      confidence: "high",
      series: "modis",
      resolution: "daily",
      ramp: "cividis",
      baseline: { start: 2005, end: 2020 },
      year: 2016,
      periodId: "p2",
      asOf: "2016-11-05",
      mapDate: "2016-11-04",
      mapWindow: 14,
      basemap: "truecolor",
    };
    expect(decodeState(encodeState(state))).toEqual(state);
  });

  it("omits defaults so links stay short", () => {
    expect(encodeState(DEFAULT_STATE)).toBe("");
    expect(encodeState({ ...DEFAULT_STATE, tab: "calendar" })).toBe("?tab=calendar");
  });

  it("ignores malformed or hostile parameters", () => {
    const state = decodeState(
      "?tab=admin&area=<script>alert(1)</script>&m=evil&cf=x&s=landsat&r=hourly&bs=2010&be=2011&y=99999&pd=p123&d=05-11-2016&md=tomorrow&mw=999&bm=mars&custom=../../x",
    );
    expect(state).toEqual(DEFAULT_STATE);
  });

  it("prefers a preset over a custom key and validates both", () => {
    expect(decodeState("?area=region:punjab-haryana&custom=abc123").customKey).toBeNull();
    expect(decodeState("?custom=abc123xyz").customKey).toBe("abc123xyz");
    expect(decodeState("?bs=2005&be=2012").baseline).toEqual({ start: 2005, end: 2012 });
  });
});
