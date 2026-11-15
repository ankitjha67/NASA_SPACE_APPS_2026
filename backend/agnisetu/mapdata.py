"""Map queries: detections for a view and date window, and the spatial VIIRS to MODIS ratio grid."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable
from typing import Any

import numpy as np

from . import firms
from .aoi import Aoi
from .boundaries import PointClassifier
from .store import EPOCH, Store

MAX_WINDOW_DAYS = 366
MAX_EXPORT_ROWS = 400_000
MAX_GRID_CELLS = 6000
RATIO_STEPS = (0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0)
MIN_MODIS_FOR_RATIO = 5


def _grid_step(bbox: tuple[float, float, float, float], max_cells: int, steps: tuple[float, ...]) -> float:
    west, south, east, north = bbox
    area = max((east - west) * (north - south), 1e-9)
    for step in steps:
        if area / (step * step) <= max_cells:
            return step
    return steps[-1]


def hotspots(
    store: Store,
    bbox: tuple[float, float, float, float],
    date_from: dt.date,
    date_to: dt.date,
    platform_keys: list[str] | None,
    min_conf: int,
    include_static: bool,
    clip: Aoi | None,
    max_points: int,
) -> dict[str, Any]:
    if date_to < date_from:
        raise ValueError("date_to must not be before date_from")
    if (date_to - date_from).days + 1 > MAX_WINDOW_DAYS:
        raise ValueError(f"The date window is limited to {MAX_WINDOW_DAYS} days")
    west, south, east, north = bbox
    if clip is not None:
        cw, cs, ce, cn = clip.bbox
        west, south, east, north = max(west, cw), max(south, cs), min(east, ce), min(north, cn)
    # None means every sensor. An empty list means the user switched every sensor off.
    platform_ids = sorted({firms.PLATFORM_BY_KEY[k].id for k in platform_keys}) if platform_keys is not None else None
    base = {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "bbox": [west, south, east, north],
        "platforms": [firms.PLATFORM_BY_ID[i].key for i in (platform_ids or range(firms.N_PLATFORMS))],
        "max_points": max_points,
    }
    if west >= east or south >= north or platform_ids == []:
        return {**base, "mode": "points", "count": 0, "total_in_view": 0, **_empty_points()}

    where = ["acq_date BETWEEN ? AND ?", "lon BETWEEN ? AND ?", "lat BETWEEN ? AND ?", "conf >= ?"]
    params: list[Any] = [date_from, date_to, west, east, south, north, int(min_conf)]
    if platform_ids is not None:
        where.append(f"platform IN ({', '.join('?' for _ in platform_ids)})")
        params.extend(platform_ids)
    if not include_static:
        where.append("coalesce(ftype, 0) = 0")
    clause = " AND ".join(where)
    cur = store.cursor()

    rough = cur.execute(f"SELECT count(*) FROM det WHERE {clause}", params).fetchone()[0]
    # When clipping, allow a larger raw fetch because many points fall outside the polygon.
    fetch_cap = max_points * (4 if clip is not None and not clip.is_rectangle else 1)
    if rough <= fetch_cap:
        rows = cur.execute(
            f"""
            SELECT lon, lat, platform, conf, frp, is_day, CAST(date_diff('day', ?, acq_date) AS INTEGER) AS day_offset,
                   acq_time, scan, track, coalesce(ftype, 0) AS ftype
            FROM det WHERE {clause}
            """,
            [date_from, *params],
        ).fetchnumpy()
        lon = np.asarray(rows["lon"], dtype=float)
        lat = np.asarray(rows["lat"], dtype=float)
        keep = np.ones(lon.size, dtype=bool)
        if clip is not None and not clip.is_rectangle and lon.size:
            keep = PointClassifier(clip.geometry, max_cells=20_000).contains(lon, lat)
        if int(keep.sum()) <= max_points:
            order = np.argsort(np.asarray(rows["frp"], dtype=float)[keep], kind="stable")
            pick = np.flatnonzero(keep)[order]
            return {
                **base,
                "mode": "points",
                "count": int(pick.size),
                "total_in_view": int(pick.size),
                "lon": np.round(lon[pick], 5).tolist(),
                "lat": np.round(lat[pick], 5).tolist(),
                "platform": np.asarray(rows["platform"], dtype=int)[pick].tolist(),
                "conf": np.asarray(rows["conf"], dtype=int)[pick].tolist(),
                "frp": np.round(np.asarray(rows["frp"], dtype=float)[pick], 1).tolist(),
                "day": np.asarray(rows["is_day"], dtype=bool)[pick].astype(int).tolist(),
                "day_offset": np.asarray(rows["day_offset"], dtype=int)[pick].tolist(),
                "time": np.asarray(rows["acq_time"], dtype=int)[pick].tolist(),
                "scan": np.round(np.asarray(rows["scan"], dtype=float)[pick], 2).tolist(),
                "track": np.round(np.asarray(rows["track"], dtype=float)[pick], 2).tolist(),
                "ftype": np.asarray(rows["ftype"], dtype=int)[pick].tolist(),
            }

    step = _grid_step((west, south, east, north), MAX_GRID_CELLS, (0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0))
    rows = cur.execute(
        f"""
        SELECT CAST(floor(lon / ?) AS INTEGER) AS cx, CAST(floor(lat / ?) AS INTEGER) AS cy,
               count(*) AS n, sum(frp) AS frp
        FROM det WHERE {clause} GROUP BY 1, 2
        """,
        [step, step, *params],
    ).fetchnumpy()
    cx = np.asarray(rows["cx"], dtype=np.int64)
    cy = np.asarray(rows["cy"], dtype=np.int64)
    n = np.asarray(rows["n"], dtype=np.int64)
    frp = np.asarray(rows["frp"], dtype=float)
    if clip is not None and not clip.is_rectangle and cx.size:
        inside = PointClassifier(clip.geometry, max_cells=20_000).contains((cx + 0.5) * step, (cy + 0.5) * step)
        cx, cy, n, frp = cx[inside], cy[inside], n[inside], frp[inside]
    return {
        **base,
        "mode": "grid",
        "count": int(cx.size),
        "total_in_view": int(n.sum()),
        "step": step,
        "west": np.round(cx * step, 4).tolist(),
        "south": np.round(cy * step, 4).tolist(),
        "n": n.tolist(),
        "frp": np.round(frp, 1).tolist(),
    }


def _empty_points() -> dict[str, list[Any]]:
    return {
        k: [] for k in ("lon", "lat", "platform", "conf", "frp", "day", "day_offset", "time", "scan", "track", "ftype")
    }


def ratio_grid(store: Store, aoi: Aoi, min_conf: int) -> dict[str, Any]:
    """VIIRS S-NPP to MODIS detection ratio per grid cell over the days both records cover."""
    coverage = store.coverage_for(aoi.geometry)
    from .store import OBSERVED, index_day

    snpp = np.flatnonzero(coverage.status[firms.SNPP] == OBSERVED)
    if snpp.size == 0:
        return {"available": False, "reason": "VIIRS S-NPP has not been ingested."}
    start, end = index_day(int(snpp[0])), index_day(int(snpp[-1]))
    step = _grid_step(aoi.bbox, MAX_GRID_CELLS, RATIO_STEPS)
    west, south, east, north = aoi.bbox
    rows = (
        store.cursor()
        .execute(
            """
        SELECT CAST(floor(lon / ?) AS INTEGER) AS cx, CAST(floor(lat / ?) AS INTEGER) AS cy,
               count(*) FILTER (WHERE platform IN (0, 1)) AS modis,
               count(*) FILTER (WHERE platform = 2) AS viirs
        FROM det
        WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ? AND ldate BETWEEN ? AND ?
          AND conf >= ? AND coalesce(ftype, 0) = 0 AND platform IN (0, 1, 2)
        GROUP BY 1, 2
        """,
            [step, step, west, east, south, north, start, end, int(min_conf)],
        )
        .fetchnumpy()
    )
    cx = np.asarray(rows["cx"], dtype=np.int64)
    cy = np.asarray(rows["cy"], dtype=np.int64)
    modis = np.asarray(rows["modis"], dtype=np.int64)
    viirs = np.asarray(rows["viirs"], dtype=np.int64)
    if cx.size and not aoi.is_rectangle:
        inside = PointClassifier(aoi.geometry, max_cells=20_000).contains((cx + 0.5) * step, (cy + 0.5) * step)
        cx, cy, modis, viirs = cx[inside], cy[inside], modis[inside], viirs[inside]
    enough = modis >= MIN_MODIS_FOR_RATIO
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(enough, viirs / np.maximum(modis, 1), np.nan)
    finite = ratio[np.isfinite(ratio)]
    total_m, total_v = int(modis.sum()), int(viirs.sum())
    return {
        "available": True,
        "step": step,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "min_modis": MIN_MODIS_FOR_RATIO,
        "west": np.round(cx * step, 4).tolist(),
        "south": np.round(cy * step, 4).tolist(),
        "modis": modis.tolist(),
        "viirs": viirs.tolist(),
        "ratio": [None if not math.isfinite(v) else round(float(v), 2) for v in ratio],
        "cells": int(cx.size),
        "cells_with_ratio": int(finite.size),
        "overall_ratio": round(total_v / total_m, 2) if total_m > 0 else None,
        "ratio_p10": round(float(np.quantile(finite, 0.10)), 2) if finite.size else None,
        "ratio_median": round(float(np.median(finite)), 2) if finite.size else None,
        "ratio_p90": round(float(np.quantile(finite, 0.90)), 2) if finite.size else None,
    }


EXPORT_COLUMNS = (
    "latitude",
    "longitude",
    "acq_date",
    "acq_time_utc",
    "local_solar_date",
    "satellite",
    "instrument",
    "confidence",
    "frp_mw",
    "daynight",
    "scan_km",
    "track_km",
    "brightness_k",
    "type",
    "quality",
    "harmonized_weight",
    "harmonized_value_type",
)
# Given day index, platform id and eligibility of each row, returns its harmonized weight and value type code.
Weigher = Callable[[np.ndarray, np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]
_VALUE_TYPE_NAMES = ("no_coverage", "observed_reference", "viirs_calibrated", "modis_harmonized")
_CONFIDENCE_NAMES = ("low", "nominal", "high")
_TYPE_NAMES = {0: "vegetation_fire", 1: "volcano", 2: "static_land_source", 3: "offshore"}


def hotspots_csv(
    store: Store,
    aoi: Aoi,
    date_from: dt.date,
    date_to: dt.date,
    platform_keys: list[str] | None,
    min_conf: int,
    include_static: bool,
    weigher: Weigher | None = None,
) -> tuple[str, int]:
    """Every detection inside an area for a date range, as CSV text. Returns (text, row count).

    With a weigher, each row also carries its harmonized weight: the number of VIIRS S-NPP equivalent
    detections it stands for in the harmonized record of this area.
    """
    import csv
    import io

    if date_to < date_from:
        raise ValueError("date_to must not be before date_from")
    if (date_to - date_from).days + 1 > MAX_WINDOW_DAYS:
        raise ValueError(f"The date range for a detection download is limited to {MAX_WINDOW_DAYS} days")
    platform_ids = sorted({firms.PLATFORM_BY_KEY[k].id for k in platform_keys}) if platform_keys is not None else None
    west, south, east, north = aoi.bbox
    where = ["acq_date BETWEEN ? AND ?", "lon BETWEEN ? AND ?", "lat BETWEEN ? AND ?", "conf >= ?"]
    params: list[Any] = [date_from, date_to, west, east, south, north, int(min_conf)]
    if platform_ids is not None:
        if not platform_ids:
            where.append("false")
        else:
            where.append(f"platform IN ({', '.join('?' for _ in platform_ids)})")
            params.extend(platform_ids)
    if not include_static:
        where.append("coalesce(ftype, 0) = 0")
    clause = " AND ".join(where)
    cur = store.cursor()
    rough = cur.execute(f"SELECT count(*) FROM det WHERE {clause}", params).fetchone()[0]
    if rough > MAX_EXPORT_ROWS * (1 if aoi.is_rectangle else 6):
        raise ValueError(
            f"This search matches about {rough:,} detections. Narrow the date range or the area to download them."
        )
    rows = cur.execute(
        f"""
        SELECT lat, lon, CAST(acq_date AS VARCHAR) AS acq_date, acq_time, CAST(ldate AS VARCHAR) AS ldate, platform,
               conf, frp, is_day, scan, track, bright, coalesce(ftype, 0) AS ftype, coalesce(is_nrt, false) AS is_nrt
        FROM det WHERE {clause} ORDER BY acq_date, acq_time, platform
        """,
        params,
    ).fetchnumpy()
    lat = np.asarray(rows["lat"], dtype=float)
    lon = np.asarray(rows["lon"], dtype=float)
    keep = np.ones(lat.size, dtype=bool)
    if lat.size and not aoi.is_rectangle:
        keep = PointClassifier(aoi.geometry, max_cells=20_000).contains(lon, lat)
    count = int(keep.sum())
    if count > MAX_EXPORT_ROWS:
        raise ValueError(
            f"This search matches {count:,} detections; the limit for one download is {MAX_EXPORT_ROWS:,}. "
            "Narrow the date range or the area."
        )
    pick = np.flatnonzero(keep)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    platform = np.asarray(rows["platform"], dtype=int)[pick]
    conf = np.asarray(rows["conf"], dtype=int)[pick]
    acq_time = np.asarray(rows["acq_time"], dtype=int)[pick]
    ftype = np.asarray(rows["ftype"], dtype=int)[pick]
    ldate_text = np.asarray(rows["ldate"], dtype=object)[pick].tolist()
    weight_text = [""] * count
    vtype_text = [""] * count
    if weigher is not None and count:
        day_index = (np.array(ldate_text, dtype="datetime64[D]") - np.datetime64(EPOCH, "D")).astype(np.int64)
        weight, vtype = weigher(day_index, platform, ftype == 0)
        weight_text = [f"{w:.4f}" for w in weight.tolist()]
        vtype_text = [_VALUE_TYPE_NAMES[int(v)] for v in vtype.tolist()]
    columns = zip(
        np.round(lat[pick], 5).tolist(),
        np.round(lon[pick], 5).tolist(),
        np.asarray(rows["acq_date"], dtype=object)[pick].tolist(),
        acq_time.tolist(),
        ldate_text,
        platform.tolist(),
        conf.tolist(),
        np.round(np.asarray(rows["frp"], dtype=float)[pick], 2).tolist(),
        np.asarray(rows["is_day"], dtype=bool)[pick].tolist(),
        np.round(np.asarray(rows["scan"], dtype=float)[pick], 2).tolist(),
        np.round(np.asarray(rows["track"], dtype=float)[pick], 2).tolist(),
        np.round(np.asarray(rows["bright"], dtype=float)[pick], 1).tolist(),
        ftype.tolist(),
        np.asarray(rows["is_nrt"], dtype=bool)[pick].tolist(),
        weight_text,
        vtype_text,
        strict=True,
    )
    for (
        la,
        lo,
        date,
        time_,
        ldate,
        plat,
        cf,
        frp,
        day,
        scan,
        track,
        bright,
        kind,
        nrt,
        weight_cell,
        vtype_cell,
    ) in columns:
        meta = firms.PLATFORM_BY_ID[plat]
        writer.writerow(
            [
                la,
                lo,
                date,
                f"{time_:04d}",
                ldate,
                meta.label,
                meta.instrument,
                _CONFIDENCE_NAMES[cf],
                frp,
                "D" if day else "N",
                scan,
                track,
                bright,
                _TYPE_NAMES.get(kind, "unknown"),
                "near_real_time" if nrt else "standard",
                weight_cell,
                vtype_cell,
            ]
        )
    return buffer.getvalue(), count
