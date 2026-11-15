"""Area of interest handling: strict GeoJSON parsing, repair, measurement and hashing."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import shapely
from shapely.geometry import MultiPolygon, Polygon, box, mapping
from shapely.geometry.base import BaseGeometry

EARTH_RADIUS_KM = 6371.0088
_COORD_DECIMALS = 5


class AoiError(ValueError):
    """Raised when a submitted geometry is unusable. The message is safe to show to the user."""


@dataclass(frozen=True)
class Aoi:
    id: str
    name: str
    geometry: BaseGeometry
    bbox: tuple[float, float, float, float]  # west, south, east, north
    area_km2: float
    is_rectangle: bool
    vertices: int
    source: str  # "preset" or "custom"
    preset_id: str | None = None
    repaired: bool = False

    def geojson(self) -> dict[str, Any]:
        return mapping(self.geometry)


def _ring_area_steradians(coords: np.ndarray) -> float:
    """Signed area of a closed ring on the unit sphere (Chamberlain and Duquette, 2007)."""
    lon = np.radians(coords[:, 0])
    lat = np.radians(coords[:, 1])
    if len(lon) < 4:
        return 0.0
    lon_next = np.roll(lon[:-1], -1)
    lon_prev = np.roll(lon[:-1], 1)
    return float(np.sum((lon_next - lon_prev) * np.sin(lat[:-1])) / 2.0)


def geodesic_area_km2(geometry: BaseGeometry) -> float:
    """Area on a sphere of authalic radius. Accurate to well under one percent for our use."""
    total = 0.0
    polygons = geometry.geoms if isinstance(geometry, MultiPolygon) else [geometry]
    for polygon in polygons:
        if polygon.is_empty:
            continue
        area = abs(_ring_area_steradians(np.asarray(polygon.exterior.coords)))
        for interior in polygon.interiors:
            area -= abs(_ring_area_steradians(np.asarray(interior.coords)))
        total += max(area, 0.0)
    return total * EARTH_RADIUS_KM**2


def count_vertices(geometry: BaseGeometry) -> int:
    return int(shapely.get_num_coordinates(geometry))


def _validate_position(position: Any) -> tuple[float, float]:
    if not isinstance(position, (list, tuple)) or len(position) < 2:
        raise AoiError("Each coordinate must be a [longitude, latitude] pair")
    lon, lat = position[0], position[1]
    if (
        isinstance(lon, bool)
        or isinstance(lat, bool)
        or not isinstance(lon, (int, float))
        or not isinstance(lat, (int, float))
    ):
        raise AoiError("Coordinates must be numbers")
    if not (math.isfinite(lon) and math.isfinite(lat)):
        raise AoiError("Coordinates must be finite numbers")
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise AoiError("Coordinates must be longitude -180 to 180 and latitude -90 to 90 (WGS84)")
    return float(lon), float(lat)


def _polygon_from_rings(rings: Any, budget: list[int]) -> Polygon:
    if not isinstance(rings, list) or not rings:
        raise AoiError("A Polygon needs at least one ring")
    parsed: list[list[tuple[float, float]]] = []
    for ring in rings:
        if not isinstance(ring, list) or len(ring) < 4:
            raise AoiError("Each polygon ring needs at least four positions, with the first repeated at the end")
        budget[0] -= len(ring)
        if budget[0] < 0:
            raise AoiError("The geometry has too many vertices")
        points = [_validate_position(p) for p in ring]
        if points[0] != points[-1]:
            points.append(points[0])
        parsed.append(points)
    return Polygon(parsed[0], parsed[1:])


def _collect_polygons(obj: Any, budget: list[int], depth: int = 0) -> list[Polygon]:
    if depth > 4 or not isinstance(obj, dict):
        raise AoiError("Unsupported GeoJSON structure")
    kind = obj.get("type")
    if kind == "Polygon":
        return [_polygon_from_rings(obj.get("coordinates"), budget)]
    if kind == "MultiPolygon":
        parts = obj.get("coordinates")
        if not isinstance(parts, list) or not parts:
            raise AoiError("A MultiPolygon needs at least one polygon")
        if len(parts) > 2000:
            raise AoiError("The geometry has too many parts")
        return [_polygon_from_rings(rings, budget) for rings in parts]
    if kind == "Feature":
        return _collect_polygons(obj.get("geometry"), budget, depth + 1)
    if kind == "FeatureCollection":
        features = obj.get("features")
        if not isinstance(features, list) or not features:
            raise AoiError("The FeatureCollection is empty")
        if len(features) > 2000:
            raise AoiError("The FeatureCollection has too many features")
        out: list[Polygon] = []
        for feature in features:
            geometry = feature.get("geometry") if isinstance(feature, dict) else None
            if isinstance(geometry, dict) and geometry.get("type") in {"Polygon", "MultiPolygon"}:
                out.extend(_collect_polygons(geometry, budget, depth + 1))
        if not out:
            raise AoiError("The FeatureCollection contains no Polygon or MultiPolygon features")
        return out
    if kind == "GeometryCollection":
        geometries = obj.get("geometries")
        if not isinstance(geometries, list):
            raise AoiError("Unsupported GeoJSON structure")
        out = []
        for geometry in geometries:
            if isinstance(geometry, dict) and geometry.get("type") in {"Polygon", "MultiPolygon"}:
                out.extend(_collect_polygons(geometry, budget, depth + 1))
        if not out:
            raise AoiError("The GeometryCollection contains no polygons")
        return out
    raise AoiError("Only Polygon and MultiPolygon areas are supported")


def _polygonal(geometry: BaseGeometry) -> BaseGeometry:
    """Keep only polygonal parts of a geometry (make_valid can emit lines and points)."""
    if geometry.is_empty:
        return geometry
    if geometry.geom_type in ("Polygon", "MultiPolygon"):
        return geometry
    if hasattr(geometry, "geoms"):
        polygons = [g for part in geometry.geoms for g in _explode(_polygonal(part))]
        if not polygons:
            return Polygon()
        return shapely.union_all(polygons)
    return Polygon()


def _explode(geometry: BaseGeometry) -> list[Polygon]:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type == "MultiPolygon":
        return list(geometry.geoms)
    return []


def normalize_geometry(geometry: BaseGeometry) -> tuple[BaseGeometry, bool]:
    """Round coordinates, repair invalid shapes, return polygonal geometry and a repaired flag."""
    rounded = shapely.set_precision(geometry, 10.0**-_COORD_DECIMALS)
    repaired = False
    if not rounded.is_valid:
        rounded = shapely.make_valid(rounded)
        repaired = True
    rounded = _polygonal(rounded)
    if rounded.is_empty:
        raise AoiError("The geometry has no area")
    return shapely.normalize(rounded), repaired


def geometry_hash(geometry: BaseGeometry) -> str:
    return hashlib.sha256(shapely.to_wkb(geometry, output_dimension=2, byte_order=1)).hexdigest()[:24]


def build_aoi(
    geometry: BaseGeometry,
    name: str,
    source: str,
    preset_id: str | None = None,
    max_vertices: int = 20_000,
    min_area_km2: float = 25.0,
    max_area_km2: float = 6_000_000.0,
    repaired: bool = False,
) -> Aoi:
    vertices = count_vertices(geometry)
    if vertices > max_vertices:
        raise AoiError(f"The geometry has {vertices} vertices; the limit is {max_vertices}")
    area = geodesic_area_km2(geometry)
    if area < min_area_km2:
        raise AoiError(f"The area is {area:,.1f} square km; the minimum is {min_area_km2:,.0f} square km")
    if area > max_area_km2:
        raise AoiError(f"The area is {area:,.0f} square km; the maximum is {max_area_km2:,.0f} square km")
    west, south, east, north = geometry.bounds
    envelope = box(west, south, east, north)
    is_rect = geometry.geom_type == "Polygon" and abs(envelope.area - geometry.area) <= 1e-9 * max(envelope.area, 1e-12)
    return Aoi(
        id=geometry_hash(geometry),
        name=name,
        geometry=geometry,
        bbox=(float(west), float(south), float(east), float(north)),
        area_km2=float(area),
        is_rectangle=bool(is_rect),
        vertices=vertices,
        source=source,
        preset_id=preset_id,
        repaired=repaired,
    )


def parse_geojson(
    obj: Any,
    name: str = "Custom area",
    max_vertices: int = 20_000,
    min_area_km2: float = 25.0,
    max_area_km2: float = 6_000_000.0,
) -> Aoi:
    """Validate untrusted GeoJSON and return a normalized area of interest."""
    budget = [max_vertices]
    polygons = _collect_polygons(obj, budget)
    try:
        merged = shapely.union_all([p if p.is_valid else shapely.make_valid(p) for p in polygons])
    except shapely.errors.GEOSException as exc:
        raise AoiError("The geometry could not be processed") from exc
    was_invalid = any(not p.is_valid for p in polygons)
    geometry, repaired = normalize_geometry(merged)
    clean_name = " ".join(str(name).split())[:80] or "Custom area"
    return build_aoi(
        geometry, clean_name, "custom", None, max_vertices, min_area_km2, max_area_km2, repaired or was_invalid
    )
