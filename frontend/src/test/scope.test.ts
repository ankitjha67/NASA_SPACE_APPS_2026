import { describe, expect, it } from "vitest";
import { RAMP_ORDER, RAMPS, rampColor, rampCss } from "../lib/colors";
import { dataUrlToBlob, fileSlug } from "../lib/download";
import { boxName, formatCompact, separatedLabels } from "../lib/format";
import { boxGeometry, circleGeometry, GeoJsonError } from "../lib/geo";
import { decodeState, DEFAULT_STATE, encodeState } from "../state/urlState";

function haversineKm(a: [number, number], b: [number, number]): number {
  const rad = Math.PI / 180;
  const dLat = (b[1] - a[1]) * rad;
  const dLon = (b[0] - a[0]) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * rad) * Math.cos(b[1] * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371.0088 * Math.asin(Math.sqrt(h));
}

describe("coordinate entry", () => {
  it("builds a closed circle whose every vertex is the radius away from the center", () => {
    const circle = circleGeometry(30.25, 75.84, 25);
    const ring = circle.coordinates[0] as [number, number][];
    expect(ring).toHaveLength(65);
    expect(ring[0]).toEqual(ring[64]);
    for (const vertex of ring) expect(haversineKm([75.84, 30.25], vertex)).toBeCloseTo(25, 1);
  });

  it("keeps longitudes in range for a circle that crosses the date line region", () => {
    const ring = circleGeometry(10, 179.9, 100).coordinates[0] as [number, number][];
    for (const [lon, lat] of ring) {
      expect(lon).toBeGreaterThanOrEqual(-180);
      expect(lon).toBeLessThanOrEqual(180);
      expect(Math.abs(lat)).toBeLessThanOrEqual(90);
    }
  });

  it("rejects impossible points and radii with a message a person can act on", () => {
    expect(() => circleGeometry(95, 75, 25)).toThrow(GeoJsonError);
    expect(() => circleGeometry(30, 200, 25)).toThrow(/Latitude must be/);
    expect(() => circleGeometry(30, 75, 0)).toThrow(/radius/);
    expect(() => circleGeometry(30, 75, 5000)).toThrow(/radius/);
    expect(() => circleGeometry(Number.NaN, 75, 25)).toThrow(GeoJsonError);
  });

  it("builds a bounding box and explains what is wrong with a bad one", () => {
    expect(boxGeometry(75, 29.5, 76.5, 31).coordinates[0]).toEqual([[75, 29.5], [76.5, 29.5], [76.5, 31], [75, 31], [75, 29.5]]);
    expect(() => boxGeometry(76.5, 29.5, 75, 31)).toThrow(/West must be smaller than east/);
    expect(() => boxGeometry(75, 31, 76.5, 29.5)).toThrow(/South must be smaller than north/);
    expect(() => boxGeometry(75, 29.5, 276.5, 31)).toThrow(/Longitude must be/);
    expect(() => boxGeometry(Number.NaN, 29.5, 76.5, 31)).toThrow(/all four numbers/);
  });
});

describe("date range and monthly view in the URL", () => {
  it("accepts any range length up to a year and the monthly resolution", () => {
    expect(decodeState("?mw=45").mapWindow).toBe(45);
    expect(decodeState("?mw=366").mapWindow).toBe(366);
    expect(decodeState("?mw=367").mapWindow).toBe(DEFAULT_STATE.mapWindow);
    expect(decodeState("?mw=0").mapWindow).toBe(DEFAULT_STATE.mapWindow);
    expect(decodeState("?mw=2.5").mapWindow).toBe(DEFAULT_STATE.mapWindow);
    expect(decodeState("?r=monthly").resolution).toBe("monthly");
    expect(encodeState({ ...DEFAULT_STATE, resolution: "monthly", mapWindow: 120, mapDate: "2016-11-15" })).toBe(
      "?r=monthly&md=2016-11-15&mw=120",
    );
  });
});

describe("saving files", () => {
  it("decodes a base64 data URL into a blob of the right type and size", async () => {
    const blob = dataUrlToBlob("data:image/png;base64,iVBORw0KGgo=");
    expect(blob.type).toBe("image/png");
    expect(blob.size).toBe(8);
    const text = dataUrlToBlob("data:text/plain,hello%20fire");
    expect(text.type).toBe("text/plain");
    expect(text.size).toBe(10);
  });

  it("makes safe file name fragments from area names", () => {
    expect(fileSlug("Punjab and Haryana")).toBe("punjab-and-haryana");
    expect(fileSlug("Within 25 km of 30.250, 75.840")).toBe("within-25-km-of-30-250-75-840");
    expect(fileSlug("../../etc/passwd")).toBe("etc-passwd");
    expect(fileSlug("")).toBe("area");
    expect(fileSlug("x".repeat(200))).toHaveLength(48);
  });
});

function luminance(rgb: string): number {
  const [r, g, b] = (rgb.match(/\d+/g) ?? []).map(Number) as [number, number, number];
  const channel = (v: number) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

describe("accessible color ramps", () => {
  it("offers four ramps that all get lighter as activity rises", () => {
    expect(RAMP_ORDER).toEqual(["fire", "viridis", "cividis", "gray"]);
    for (const key of RAMP_ORDER) {
      const steps = Array.from({ length: 21 }, (_, i) => luminance(rampColor(key, (i / 20) ** 2 * 100, 100)));
      for (let i = 1; i < steps.length; i += 1) {
        expect(steps[i]!, `${key} step ${i}`).toBeGreaterThan(steps[i - 1]! - 1e-9);
      }
      expect(steps[20]! / (steps[0]! + 0.05)).toBeGreaterThan(4);
      expect(rampCss(key)).toContain("linear-gradient");
      expect(RAMPS[key].label.length).toBeGreaterThan(0);
    }
  });

  it("keeps the chosen ramp in the address", () => {
    expect(decodeState("?cr=cividis").ramp).toBe("cividis");
    expect(decodeState("?cr=rainbow").ramp).toBe("fire");
    expect(encodeState({ ...DEFAULT_STATE, tab: "calendar", ramp: "viridis" })).toContain("cr=viridis");
    expect(encodeState(DEFAULT_STATE)).not.toContain("cr=");
  });
});

describe("axis numbers", () => {
  it("never shows a decimal on a whole number", () => {
    expect(formatCompact(0)).toBe("0");
    expect(formatCompact(300)).toBe("300");
    expect(formatCompact(2.5)).toBe("2.5");
    expect(formatCompact(12000)).toBe("12K");
  });
});

describe("reference line labels", () => {
  it("drops a label that would touch a more important one", () => {
    expect(separatedLabels([36000, 34800], 57000)).toEqual([true, false]);
    expect(separatedLabels([64000, 82000, 35000], 100000)).toEqual([true, true, true]);
    expect(separatedLabels([10, 10.2, 2], 12)).toEqual([true, false, true]);
    expect(separatedLabels([null, 5], 10)).toEqual([false, true]);
    expect(separatedLabels([1, 2], 0)).toEqual([false, false]);
  });
});

describe("coordinate box names", () => {
  it("uses hemisphere letters in every quadrant", () => {
    expect(boxName(75, 29.5, 76.5, 31)).toBe("Box 29.5 to 31 N, 75 to 76.5 E");
    expect(boxName(-68, -30, -67, -29)).toBe("Box 29 to 30 S, 67 to 68 W");
    expect(boxName(-1, -2, 3, 4)).toBe("Box 2 S to 4 N, 1 W to 3 E");
  });
});
