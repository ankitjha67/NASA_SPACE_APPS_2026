"""Per area daily aggregation ("cube") with a two level cache.

A cube holds, for one area, the number of vegetation fire detections and the summed fire
radiative power for every day, platform, confidence class and day or night flag. Everything
downstream (harmonization, calendar, anomalies, warning) is computed from this small array, so
changing the metric or the confidence filter never touches the raw detections again.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import firms
from .aoi import Aoi
from .boundaries import PointClassifier
from .store import EPOCH, EPOCH_SQL, Store, day_index

log = logging.getLogger("agnisetu.cube")

N_CONF = 3
N_DAYNIGHT = 2
_CHUNK_DEG2 = 60.0  # bounding boxes larger than this are streamed year by year


@dataclass(frozen=True)
class Cube:
    aoi_id: str
    revision: int
    n_days: int
    counts: np.ndarray  # uint32 [day, platform, conf, daynight], vegetation fire detections
    frp: np.ndarray  # float32 [day, platform, conf, daynight], megawatts
    static_counts: np.ndarray  # int64 [platform], detections flagged as static sources
    build_seconds: float

    def series(self, metric: str, min_conf: int) -> np.ndarray:
        """Daily values per platform, float64 [platform, day], for a metric and confidence floor."""
        source = self.counts if metric == "count" else self.frp
        return source[:, :, min_conf:, :].sum(axis=(2, 3), dtype=np.float64).T

    def count_series(self, min_conf: int) -> np.ndarray:
        return self.counts[:, :, min_conf:, :].sum(axis=(2, 3), dtype=np.float64).T

    def totals_by_platform(self, min_conf: int = 0) -> np.ndarray:
        return self.counts[:, :, min_conf:, :].sum(axis=(0, 2, 3), dtype=np.int64)


class CubeBuilder:
    def __init__(self, store: Store, cache_dir: Path, memory_entries: int = 48, disk_entries: int = 400) -> None:
        self.store = store
        self.cache_dir = cache_dir / "cubes"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory: OrderedDict[tuple[str, int], Cube] = OrderedDict()
        self._memory_entries = memory_entries
        self._disk_entries = disk_entries
        self._lock = threading.Lock()
        self._inflight: dict[tuple[str, int], threading.Event] = {}

    # ------------------------------------------------------------------ public

    def get(self, aoi: Aoi) -> Cube:
        key = (aoi.id, self.store.revision)
        while True:
            with self._lock:
                cube = self._memory.get(key)
                if cube is not None:
                    self._memory.move_to_end(key)
                    return cube
                waiter = self._inflight.get(key)
                if waiter is None:
                    self._inflight[key] = threading.Event()
                    break
            waiter.wait(timeout=300)
        try:
            cube = self._load_disk(key) or self._build(aoi, key[1])
            with self._lock:
                self._memory[key] = cube
                self._memory.move_to_end(key)
                while len(self._memory) > self._memory_entries:
                    self._memory.popitem(last=False)
            return cube
        finally:
            with self._lock:
                event = self._inflight.pop(key, None)
            if event is not None:
                event.set()

    def get_rect(self, bbox: tuple[float, float, float, float]) -> Cube:
        """Cube for a plain bounding box, used as a wider training scope for calibration."""
        from shapely.geometry import box

        from .aoi import build_aoi, normalize_geometry

        geometry, _ = normalize_geometry(box(*bbox))
        aoi = build_aoi(geometry, "training scope", "custom", max_vertices=10, min_area_km2=0.0, max_area_km2=1e12)
        return self.get(aoi)

    def purge_stale(self) -> None:
        """Remove disk cache files from older store revisions and cap the cache size."""
        suffix = f"_{self.store.revision}.npz"
        files = sorted(self.cache_dir.glob("*.npz"), key=lambda f: f.stat().st_mtime, reverse=True)
        keep = 0
        for file in files:
            if file.name.endswith(suffix) and keep < self._disk_entries:
                keep += 1
                continue
            file.unlink(missing_ok=True)
        with self._lock:
            for key in [k for k in self._memory if k[1] != self.store.revision]:
                self._memory.pop(key, None)

    # ------------------------------------------------------------------ internals

    def _disk_path(self, key: tuple[str, int]) -> Path:
        return self.cache_dir / f"{key[0]}_{key[1]}.npz"

    def _load_disk(self, key: tuple[str, int]) -> Cube | None:
        path = self._disk_path(key)
        if not path.exists():
            return None
        try:
            with np.load(path, allow_pickle=False) as data:
                counts = data["counts"]
                n_days = self.store.coverage().n_days
                if counts.shape != (n_days, firms.N_PLATFORMS, N_CONF, N_DAYNIGHT):
                    return None
                return Cube(key[0], key[1], n_days, counts, data["frp"], data["static_counts"], 0.0)
        except (OSError, KeyError, ValueError):
            path.unlink(missing_ok=True)
            return None

    def _save_disk(self, key: tuple[str, int], cube: Cube) -> None:
        path = self._disk_path(key)
        tmp = path.with_suffix(".tmp.npz")
        try:
            np.savez_compressed(tmp, counts=cube.counts, frp=cube.frp, static_counts=cube.static_counts)
            tmp.replace(path)
        except OSError:
            log.warning("Could not write cube cache file %s", path.name)
            tmp.unlink(missing_ok=True)

    def _build(self, aoi: Aoi, revision: int) -> Cube:
        started = time.perf_counter()
        n_days = self.store.coverage().n_days
        shape = (n_days, firms.N_PLATFORMS, N_CONF, N_DAYNIGHT)
        size = int(np.prod(shape))
        counts = np.zeros(size, dtype=np.int64)
        frp = np.zeros(size, dtype=np.float64)
        static_counts = np.zeros(firms.N_PLATFORMS, dtype=np.int64)
        west, south, east, north = aoi.bbox
        cur = self.store.cursor()

        if aoi.is_rectangle:
            rows = cur.execute(
                f"""
                SELECT CAST(date_diff('day', {EPOCH_SQL}, ldate) AS INTEGER) AS d, platform, conf, is_day,
                       coalesce(ftype, 0) = 0 AS is_fire, count(*) AS n, sum(frp) AS f
                FROM det WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?
                GROUP BY ALL
                """,
                [west, east, south, north],
            ).fetchnumpy()
            self._accumulate(
                rows["d"],
                rows["platform"],
                rows["conf"],
                rows["is_day"],
                rows["is_fire"],
                rows["n"],
                rows["f"],
                counts,
                frp,
                static_counts,
                n_days,
            )
        else:
            classifier = PointClassifier(aoi.geometry)
            for lo, hi in self._date_chunks(aoi, n_days):
                rows = cur.execute(
                    f"""
                    SELECT lon, lat, CAST(date_diff('day', {EPOCH_SQL}, ldate) AS INTEGER) AS d, platform, conf,
                           is_day, coalesce(ftype, 0) = 0 AS is_fire, frp
                    FROM det WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ? AND acq_date BETWEEN ? AND ?
                    """,
                    [west, east, south, north, lo, hi],
                ).fetchnumpy()
                if len(rows["lon"]) == 0:
                    continue
                inside = classifier.contains(np.asarray(rows["lon"], dtype=float), np.asarray(rows["lat"], dtype=float))
                if not np.any(inside):
                    continue
                self._accumulate(
                    np.asarray(rows["d"])[inside],
                    np.asarray(rows["platform"])[inside],
                    np.asarray(rows["conf"])[inside],
                    np.asarray(rows["is_day"])[inside],
                    np.asarray(rows["is_fire"])[inside],
                    None,
                    np.asarray(rows["frp"])[inside],
                    counts,
                    frp,
                    static_counts,
                    n_days,
                )
        cube = Cube(
            aoi_id=aoi.id,
            revision=revision,
            n_days=n_days,
            counts=counts.reshape(shape).astype(np.uint32),
            frp=frp.reshape(shape).astype(np.float32),
            static_counts=static_counts,
            build_seconds=round(time.perf_counter() - started, 3),
        )
        self._save_disk((aoi.id, revision), cube)
        log.info("Built cube for %s (%s, %.0f km2) in %.2fs", aoi.id, aoi.name, aoi.area_km2, cube.build_seconds)
        return cube

    @staticmethod
    def _date_chunks(aoi: Aoi, n_days: int) -> list[tuple[dt.date, dt.date]]:
        west, south, east, north = aoi.bbox
        last = EPOCH + dt.timedelta(days=n_days + 1)
        first = EPOCH - dt.timedelta(days=1)
        if (east - west) * (north - south) <= _CHUNK_DEG2:
            return [(first, last)]
        chunks = []
        for year in range(first.year, last.year + 1):
            chunks.append((max(first, dt.date(year, 1, 1)), min(last, dt.date(year, 12, 31))))
        return chunks

    @staticmethod
    def _accumulate(
        d: np.ndarray,
        platform: np.ndarray,
        conf: np.ndarray,
        is_day: np.ndarray,
        is_fire: np.ndarray,
        n: np.ndarray | None,
        f: np.ndarray,
        counts: np.ndarray,
        frp: np.ndarray,
        static_counts: np.ndarray,
        n_days: int,
    ) -> None:
        d = np.asarray(d, dtype=np.int64)
        platform = np.asarray(platform, dtype=np.int64)
        conf = np.asarray(conf, dtype=np.int64)
        day_flag = np.asarray(is_day, dtype=bool).astype(np.int64)
        fire = np.asarray(is_fire, dtype=bool)
        weights = np.ones(d.size, dtype=np.int64) if n is None else np.asarray(n, dtype=np.int64)
        power = np.nan_to_num(np.asarray(f, dtype=np.float64), nan=0.0)
        valid = (d >= 0) & (d < n_days)
        static = valid & ~fire
        if np.any(static):
            static_counts += np.bincount(platform[static], weights=weights[static], minlength=firms.N_PLATFORMS).astype(
                np.int64
            )
        keep = valid & fire
        if not np.any(keep):
            return
        flat = ((d[keep] * firms.N_PLATFORMS + platform[keep]) * N_CONF + conf[keep]) * N_DAYNIGHT + day_flag[keep]
        counts += np.bincount(flat, weights=weights[keep], minlength=counts.size).astype(np.int64)
        frp += np.bincount(flat, weights=power[keep], minlength=frp.size)


def day_of(day: dt.date) -> int:
    return day_index(day)
