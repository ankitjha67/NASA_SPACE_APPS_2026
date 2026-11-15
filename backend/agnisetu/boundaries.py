"""Preset areas (regions, countries, states, districts) and the analysis domain geometry."""

from __future__ import annotations

import json
import threading
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from . import firms
from .aoi import geodesic_area_km2, normalize_geometry

ASSET = Path(__file__).resolve().parent / "assets" / "boundaries.json"
DOMAIN_BUFFER_DEG = 0.02


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    description: str
    members: tuple[str, ...]


# Curated multi state regions with a distinct fire regime. Members are state preset ids.
REGIONS: tuple[Region, ...] = (
    Region(
        "region:punjab-haryana",
        "Punjab and Haryana",
        "Rice and wheat residue burning belt upwind of Delhi. Two seasons: April to May and October to November.",
        ("adm1:IND:punjab", "adm1:IND:haryana"),
    ),
    Region(
        "region:delhi-upwind-airshed",
        "Delhi upwind airshed",
        "Punjab, Haryana, Chandigarh and Delhi: the source region for post monsoon smoke over the capital.",
        ("adm1:IND:punjab", "adm1:IND:haryana", "adm1:IND:chandigarh", "adm1:IND:delhi"),
    ),
    Region(
        "region:punjab-transboundary",
        "Punjab, India and Pakistan",
        "Both Punjabs share the same cropping system and the same burning calendar across the border.",
        ("adm1:IND:punjab", "adm1:PAK:punjab"),
    ),
    Region(
        "region:upper-gangetic-plain",
        "Upper Indo-Gangetic Plain",
        "Punjab, Haryana, Chandigarh, Delhi and Uttar Pradesh.",
        ("adm1:IND:punjab", "adm1:IND:haryana", "adm1:IND:chandigarh", "adm1:IND:delhi", "adm1:IND:uttar-pradesh"),
    ),
    Region(
        "region:central-india",
        "Central Indian forests",
        "Madhya Pradesh, Chhattisgarh and Odisha: dry deciduous forest fires from February to May.",
        ("adm1:IND:madhya-pradesh", "adm1:IND:chhattisgarh", "adm1:IND:odisha"),
    ),
    Region(
        "region:northeast-india",
        "Northeast India",
        "Shifting cultivation burning from February to April across seven states.",
        (
            "adm1:IND:arunachal-pradesh",
            "adm1:IND:assam",
            "adm1:IND:manipur",
            "adm1:IND:meghalaya",
            "adm1:IND:mizoram",
            "adm1:IND:nagaland",
            "adm1:IND:tripura",
        ),
    ),
    Region(
        "region:western-himalaya",
        "Western Himalaya",
        "Uttarakhand and Himachal Pradesh: pine forest fires before the monsoon.",
        ("adm1:IND:uttarakhand", "adm1:IND:himachal-pradesh"),
    ),
    Region(
        "region:argentine-chaco",
        "Argentine Gran Chaco",
        "Chaco, Formosa, Santiago del Estero and Salta: dry forest, pasture and land clearing fires.",
        ("adm1:ARG:chaco", "adm1:ARG:formosa", "adm1:ARG:santiago-del-estero", "adm1:ARG:salta"),
    ),
    Region(
        "region:argentine-northwest",
        "Northwest Argentina",
        "La Rioja, Catamarca, Tucuman, Salta and Jujuy: dry valleys, foothill forest and sugar cane.",
        (
            "adm1:ARG:la-rioja",
            "adm1:ARG:catamarca",
            "adm1:ARG:tucuman",
            "adm1:ARG:salta",
            "adm1:ARG:jujuy",
        ),
    ),
    Region(
        "region:argentine-mesopotamia",
        "Argentine Mesopotamia",
        "Corrientes, Entre Rios and Misiones: wetland, grassland and plantation fires between the great rivers.",
        ("adm1:ARG:corrientes", "adm1:ARG:entre-rios", "adm1:ARG:misiones"),
    ),
    Region(
        "region:pampa-north-patagonia",
        "La Pampa and northern Patagonia",
        "La Pampa, Rio Negro and Neuquen: summer grassland, shrubland and forest fires.",
        ("adm1:ARG:la-pampa", "adm1:ARG:rio-negro", "adm1:ARG:neuquen"),
    ),
)

_LEVEL_ORDER = {"region": 0, "country": 1, "state": 2, "district": 3}


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


class _Catalog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded = False
        self.features: dict[str, dict[str, Any]] = {}
        self.index: list[dict[str, Any]] = []
        self.sources: list[dict[str, Any]] = []
        self._geometry_cache: dict[str, BaseGeometry] = {}

    def load(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            if not ASSET.exists():
                raise RuntimeError("Boundary asset missing. Run scripts/build_boundaries.py")
            with ASSET.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
            self.sources = payload.get("sources", [])
            for feature in payload["features"]:
                self.features[feature["id"]] = feature
            index: list[dict[str, Any]] = []
            for region in REGIONS:
                if not all(m in self.features for m in region.members):
                    continue
                geometry = self._union(region.members)
                index.append(self._entry(region.id, region.name, "region", None, None, geometry, region.description))
            countries = sorted({f["country"] for f in self.features.values()})
            for country in countries:
                members = tuple(
                    f["id"]
                    for f in self.features.values()
                    if f["country"] == country and f["level"] == "state" and f.get("listed", True)
                )
                geometry = self._union(members)
                label = firms.COUNTRIES[country].label
                iso3 = firms.COUNTRIES[country].iso3
                index.append(self._entry(f"country:{iso3}", label, "country", None, None, geometry, "Whole country"))
            for feature in self.features.values():
                if not feature.get("listed", True):
                    continue
                index.append(
                    {
                        "id": feature["id"],
                        "name": feature["name"],
                        "level": feature["level"],
                        "country": feature["country_label"],
                        "parent": feature.get("parent_name"),
                        "bbox": feature["bbox"],
                        "area_km2": feature["area_km2"],
                        "description": None,
                    }
                )
            for entry in index:
                entry["_search"] = _fold(f"{entry['name']} {entry.get('parent') or ''} {entry.get('country') or ''}")
                entry["_name"] = _fold(entry["name"])
            index.sort(key=lambda e: (_LEVEL_ORDER[e["level"]], e["_name"]))
            self.index = index
            self._loaded = True

    def _entry(
        self,
        pid: str,
        name: str,
        level: str,
        country: str | None,
        parent: str | None,
        geometry: BaseGeometry,
        description: str | None,
    ) -> dict[str, Any]:
        self._geometry_cache[pid] = geometry
        west, south, east, north = geometry.bounds
        return {
            "id": pid,
            "name": name,
            "level": level,
            "country": country,
            "parent": parent,
            "bbox": [round(west, 4), round(south, 4), round(east, 4), round(north, 4)],
            "area_km2": round(geodesic_area_km2(geometry), 1),
            "description": description,
        }

    def _union(self, member_ids: tuple[str, ...]) -> BaseGeometry:
        merged = shapely.union_all([shape(self.features[m]["geometry"]) for m in member_ids])
        geometry, _ = normalize_geometry(merged)
        return geometry

    def geometry(self, preset_id: str) -> BaseGeometry:
        self.load()
        with self._lock:
            cached = self._geometry_cache.get(preset_id)
            if cached is not None:
                return cached
            feature = self.features.get(preset_id)
            if feature is None or not feature.get("listed", True):
                raise KeyError(preset_id)
            geometry, _ = normalize_geometry(shape(feature["geometry"]))
            self._geometry_cache[preset_id] = geometry
            return geometry


_catalog = _Catalog()


def public_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if not k.startswith("_")}


def list_presets(query: str = "", level: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    _catalog.load()
    needle = _fold(query)
    out: list[tuple[int, dict[str, Any]]] = []
    for entry in _catalog.index:
        if level and entry["level"] != level:
            continue
        if needle:
            if needle not in entry["_search"]:
                continue
            rank = 0 if entry["_name"].startswith(needle) else (1 if needle in entry["_name"] else 2)
        else:
            rank = 0
        out.append((rank, entry))
    out.sort(key=lambda item: (item[0], _LEVEL_ORDER[item[1]["level"]], item[1]["_name"]))
    return [public_entry(e) for _, e in out[: max(1, min(limit, 500))]]


def get_preset(preset_id: str) -> dict[str, Any]:
    _catalog.load()
    for entry in _catalog.index:
        if entry["id"] == preset_id:
            return public_entry(entry)
    raise KeyError(preset_id)


def preset_geometry(preset_id: str) -> BaseGeometry:
    return _catalog.geometry(preset_id)


@lru_cache(maxsize=1)
def reference_layer() -> dict[str, Any]:
    """Simplified state outlines with one label point each, for orientation on the map."""
    _catalog.load()
    features = []
    for feature in _catalog.features.values():
        if feature["level"] != "state" or not feature.get("listed", True):
            continue
        geometry = shapely.simplify(shape(feature["geometry"]), 0.02, preserve_topology=True)
        if geometry.is_empty:
            continue
        parts = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
        largest = max(parts, key=lambda g: g.area)
        point = largest.representative_point()
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": feature["id"],
                    "name": feature["name"],
                    "country": feature["country_label"],
                    "label_lon": round(point.x, 4),
                    "label_lat": round(point.y, 4),
                    "area_km2": feature["area_km2"],
                },
                "geometry": json.loads(shapely.to_geojson(shapely.set_precision(geometry, 0.001))),
            }
        )
    return {"type": "FeatureCollection", "features": features}


def boundary_sources() -> list[dict[str, Any]]:
    _catalog.load()
    return list(_catalog.sources)


@lru_cache(maxsize=8)
def _domain(countries: tuple[str, ...]) -> BaseGeometry:
    _catalog.load()
    parts = [
        shape(f["geometry"]) for f in _catalog.features.values() if f["country"] in countries and f["level"] == "state"
    ]
    if not parts:
        raise RuntimeError(f"No boundaries built for {', '.join(countries)}. Run scripts/build_boundaries.py")
    merged = shapely.union_all(parts).buffer(DOMAIN_BUFFER_DEG)
    geometry, _ = normalize_geometry(shapely.simplify(merged, 0.005, preserve_topology=True))
    return geometry


def domain_geometry(countries: list[str] | tuple[str, ...]) -> BaseGeometry:
    """Union of the ingested countries, slightly buffered so coastal detections are kept."""
    return _domain(tuple(sorted(firms.get_country(c).name for c in countries)))


def region_geometries(countries: list[str] | tuple[str, ...]) -> dict[str, BaseGeometry]:
    """The domain split by FIRMS region. Regions can be on different continents."""
    return {region: domain_geometry(members) for region, members in firms.regions_of(countries).items()}


def home_region(geometry: BaseGeometry, countries: list[str] | tuple[str, ...]) -> str:
    """Name of the FIRMS region an area lies in: the one it overlaps most, else the closest."""
    regions = region_geometries(countries)
    if len(regions) == 1:
        return next(iter(regions))
    best = max(regions, key=lambda name: shapely.intersection(regions[name], geometry).area)
    if shapely.intersects(regions[best], geometry):
        return best
    return min(regions, key=lambda name: shapely.distance(regions[name], geometry))


def home_region_bounds(
    geometry: BaseGeometry, countries: list[str] | tuple[str, ...]
) -> tuple[float, float, float, float]:
    """Bounding box of the part of the domain an area belongs to.

    When a calibration has to borrow data from "the whole domain", that means the region around
    the area, not a box spanning every continent the deployment happens to hold.
    """
    return region_geometries(countries)[home_region(geometry, countries)].bounds


class PointClassifier:
    """Fast point in polygon for millions of points.

    The polygon's bounding box is cut into a grid. Cells fully inside or fully outside the
    polygon answer immediately; only points in cells that touch the boundary need an exact
    test. Points on the boundary count as inside.
    """

    def __init__(self, geometry: BaseGeometry, max_cells: int = 60_000) -> None:
        # Work on a private copy. Preparing a geometry mutates it, and GEOS prepared geometries
        # are not safe to build or query from several threads at once; preset geometries are
        # shared between requests, so the shared object must never be prepared.
        geometry = shapely.from_wkb(shapely.to_wkb(geometry))
        self.geometry = geometry
        shapely.prepare(geometry)
        west, south, east, north = geometry.bounds
        self.west, self.south = float(west), float(south)
        span_x = max(float(east - west), 1e-9)
        span_y = max(float(north - south), 1e-9)
        self.step = max(0.005, float(np.sqrt(span_x * span_y / max_cells)))
        self.nx = max(1, int(np.ceil(span_x / self.step)))
        self.ny = max(1, int(np.ceil(span_y / self.step)))
        gx, gy = np.meshgrid(self.west + self.step * np.arange(self.nx), self.south + self.step * np.arange(self.ny))
        cells = shapely.box(gx, gy, gx + self.step, gy + self.step)
        inside = shapely.contains_properly(geometry, cells)
        touching = shapely.intersects(geometry, cells)
        self.cell_class = np.where(inside, 2, np.where(touching, 1, 0)).astype(np.uint8)

    def contains(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        lon = np.asarray(lon, dtype=float)
        lat = np.asarray(lat, dtype=float)
        result = np.zeros(lon.shape, dtype=bool)
        if lon.size == 0:
            return result
        ix = np.floor((lon - self.west) / self.step).astype(np.int64)
        iy = np.floor((lat - self.south) / self.step).astype(np.int64)
        # Points exactly on the east or north edge belong to the last cell.
        ix[ix == self.nx] = self.nx - 1
        iy[iy == self.ny] = self.ny - 1
        valid = (ix >= 0) & (ix < self.nx) & (iy >= 0) & (iy < self.ny)
        classes = np.zeros(lon.shape, dtype=np.uint8)
        classes[valid] = self.cell_class[iy[valid], ix[valid]]
        result[classes == 2] = True
        edge = classes == 1
        if np.any(edge):
            result[edge] = shapely.intersects_xy(self.geometry, lon[edge], lat[edge])
        return result


def contains_points(geometry: BaseGeometry, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Vectorized point in polygon test. Points on the boundary count as inside."""
    if lon.size == 0:
        return np.zeros(0, dtype=bool)
    if lon.size > 20_000:
        return PointClassifier(geometry).contains(lon, lat)
    private = shapely.from_wkb(shapely.to_wkb(geometry))
    shapely.prepare(private)
    return np.asarray(shapely.intersects_xy(private, lon, lat), dtype=bool)
