import type { AreaGeometry, Position } from "../api/types";

export const MAX_UPLOAD_BYTES = 512 * 1024;

export class GeoJsonError extends Error {}

function isPosition(value: unknown): value is Position {
  return (
    Array.isArray(value) &&
    value.length >= 2 &&
    typeof value[0] === "number" &&
    typeof value[1] === "number" &&
    Number.isFinite(value[0]) &&
    Number.isFinite(value[1])
  );
}

function cleanRing(ring: unknown): Position[] {
  if (!Array.isArray(ring) || ring.length < 4) {
    throw new GeoJsonError("Each polygon ring needs at least four positions.");
  }
  return ring.map((p) => {
    if (!isPosition(p)) throw new GeoJsonError("Coordinates must be [longitude, latitude] number pairs.");
    if (p[0] < -180 || p[0] > 180 || p[1] < -90 || p[1] > 90) {
      throw new GeoJsonError("Coordinates must be WGS84 longitude and latitude in degrees.");
    }
    return [p[0], p[1]] as Position;
  });
}

function cleanPolygon(rings: unknown): Position[][] {
  if (!Array.isArray(rings) || rings.length === 0) throw new GeoJsonError("A polygon needs at least one ring.");
  return rings.map(cleanRing);
}

function collect(node: unknown, out: Position[][][], depth: number): void {
  if (depth > 4 || typeof node !== "object" || node === null) return;
  const obj = node as Record<string, unknown>;
  switch (obj.type) {
    case "Polygon":
      out.push(cleanPolygon(obj.coordinates));
      break;
    case "MultiPolygon":
      if (!Array.isArray(obj.coordinates)) throw new GeoJsonError("Invalid MultiPolygon.");
      for (const part of obj.coordinates) out.push(cleanPolygon(part));
      break;
    case "Feature":
      collect(obj.geometry, out, depth + 1);
      break;
    case "FeatureCollection":
      if (Array.isArray(obj.features)) for (const f of obj.features) collect(f, out, depth + 1);
      break;
    case "GeometryCollection":
      if (Array.isArray(obj.geometries)) for (const g of obj.geometries) collect(g, out, depth + 1);
      break;
    default:
      break;
  }
}

/** Extract polygonal geometry from arbitrary GeoJSON text. The server validates it again. */
export function parseGeoJsonText(text: string): AreaGeometry {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new GeoJsonError("The file is not valid JSON.");
  }
  const polygons: Position[][][] = [];
  collect(parsed, polygons, 0);
  if (polygons.length === 0) {
    throw new GeoJsonError("No Polygon or MultiPolygon geometry was found in the file.");
  }
  const first = polygons[0];
  if (polygons.length === 1 && first) return { type: "Polygon", coordinates: first };
  return { type: "MultiPolygon", coordinates: polygons };
}

export function rectangleGeometry(a: Position, b: Position): AreaGeometry {
  const west = Math.min(a[0], b[0]);
  const east = Math.max(a[0], b[0]);
  const south = Math.min(a[1], b[1]);
  const north = Math.max(a[1], b[1]);
  return {
    type: "Polygon",
    coordinates: [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
  };
}

export function polygonGeometry(vertices: Position[]): AreaGeometry {
  const first = vertices[0];
  if (!first || vertices.length < 3) throw new GeoJsonError("A polygon needs at least three vertices.");
  return { type: "Polygon", coordinates: [[...vertices, first]] };
}

export function cellPolygon(west: number, south: number, step: number): Position[][] {
  return [[[west, south], [west + step, south], [west + step, south + step], [west, south + step], [west, south]]];
}

/** Short, stable id for a custom geometry, used as a storage key and in the URL. */
export function geometryKey(geometry: AreaGeometry): string {
  const text = JSON.stringify(geometry);
  let h1 = 0x811c9dc5;
  let h2 = 0x01000193;
  for (let i = 0; i < text.length; i += 1) {
    const c = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 0x01000193) >>> 0;
    h2 = Math.imul(h2 + c, 0x85ebca6b) >>> 0;
  }
  return `${h1.toString(36)}${h2.toString(36)}`;
}

const EARTH_RADIUS_KM = 6371.0088;

/** A circle of the given radius around a point, as a 64 sided polygon on the sphere. */
export function circleGeometry(lat: number, lon: number, radiusKm: number, steps = 64): AreaGeometry {
  if (!(lat >= -89.5 && lat <= 89.5) || !(lon >= -180 && lon <= 180)) {
    throw new GeoJsonError("Latitude must be between -89.5 and 89.5 and longitude between -180 and 180.");
  }
  if (!(radiusKm >= 1 && radiusKm <= 1000)) {
    throw new GeoJsonError("The radius must be between 1 and 1,000 km.");
  }
  const angular = radiusKm / EARTH_RADIUS_KM;
  const phi = (lat * Math.PI) / 180;
  const lambda = (lon * Math.PI) / 180;
  const ring: Position[] = [];
  for (let i = 0; i < steps; i += 1) {
    const bearing = (2 * Math.PI * i) / steps;
    const phi2 = Math.asin(Math.sin(phi) * Math.cos(angular) + Math.cos(phi) * Math.sin(angular) * Math.cos(bearing));
    const lambda2 =
      lambda + Math.atan2(Math.sin(bearing) * Math.sin(angular) * Math.cos(phi), Math.cos(angular) - Math.sin(phi) * Math.sin(phi2));
    const lonDeg = (((lambda2 * 180) / Math.PI + 540) % 360) - 180;
    ring.push([Number(lonDeg.toFixed(5)), Number(((phi2 * 180) / Math.PI).toFixed(5))]);
  }
  const first = ring[0];
  if (!first) throw new GeoJsonError("The circle could not be built.");
  return { type: "Polygon", coordinates: [[...ring, first]] };
}

/** A latitude and longitude box. Throws a message a person can act on when the numbers are off. */
export function boxGeometry(west: number, south: number, east: number, north: number): AreaGeometry {
  if (![west, south, east, north].every(Number.isFinite)) {
    throw new GeoJsonError("Enter all four numbers.");
  }
  if (west < -180 || east > 180 || south < -90 || north > 90) {
    throw new GeoJsonError("Longitude must be between -180 and 180 and latitude between -90 and 90.");
  }
  if (!(west < east)) throw new GeoJsonError("West must be smaller than east.");
  if (!(south < north)) throw new GeoJsonError("South must be smaller than north.");
  return rectangleGeometry([west, south], [east, north]);
}
