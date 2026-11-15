"""Query layer over the Parquet store, plus the per platform coverage model.

The store is read through one in memory DuckDB database. Archive files are exposed as a view
over Parquet. Near real time day files are small and numerous, so they are loaded into an in
memory table at refresh time. Each thread gets its own cursor.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from dataclasses import dataclass
from typing import Any

import duckdb
import numpy as np
from shapely.geometry.base import BaseGeometry

from . import firms
from .boundaries import home_region, region_geometries
from .config import Settings, get_settings
from .ingest import archive_path, archive_through, day_state, manifest_for, nrt_path

log = logging.getLogger("agnisetu.store")

EPOCH = firms.RECORD_START  # day index 0 of every time axis in the application
EPOCH_SQL = f"DATE '{EPOCH.isoformat()}'"

# Coverage states for one platform on one day.
PRE_LAUNCH, NOT_INGESTED, OUTAGE, OBSERVED, PROVISIONAL = 0, 1, 2, 3, 4
COVERAGE_LABELS = {
    PRE_LAUNCH: "pre_launch",
    NOT_INGESTED: "not_ingested",
    OUTAGE: "outage",
    OBSERVED: "observed",
    PROVISIONAL: "provisional",
}

# A silent run of days is an outage when the detections the platform normally reports on those
# calendar days (median of the other years) add up to at least this many. Under a Poisson model
# an expected count of 20 leaves a chance of about 2 in a billion of seeing none.
OUTAGE_EXPECTED_MIN = 20.0
OUTAGE_POOL_DAYS = 7
OUTAGE_MIN_YEARS = 3

_SELECT_COLUMNS = ", ".join(firms.STORE_COLUMNS)
_EMPTY_SCHEMA = (
    "lat DOUBLE, lon DOUBLE, acq_date DATE, acq_time SMALLINT, ldate DATE, platform TINYINT, conf TINYINT, "
    "frp FLOAT, is_day BOOLEAN, scan FLOAT, track FLOAT, bright FLOAT, ftype TINYINT, is_nrt BOOLEAN"
)


def day_index(day: dt.date) -> int:
    return (day - EPOCH).days


def index_day(index: int) -> dt.date:
    return EPOCH + dt.timedelta(days=int(index))


@dataclass(frozen=True)
class Coverage:
    """Coverage state and daily counts per platform over the full time axis, for one region or the domain."""

    n_days: int
    status: np.ndarray  # uint8 [platform, day]
    domain_counts: np.ndarray  # int64 [platform, day], all detection types
    last_day: dt.date
    last_complete_day: dt.date

    def observed(self, platform_id: int) -> np.ndarray:
        return self.status[platform_id] == OBSERVED

    def segments(self, platform_id: int) -> list[dict[str, Any]]:
        """Run length encoding of the state sequence, for the coverage timeline chart."""
        row = self.status[platform_id]
        if row.size == 0:
            return []
        change = np.flatnonzero(np.diff(row)) + 1
        starts = np.concatenate(([0], change))
        ends = np.concatenate((change, [row.size]))
        return [
            {
                "state": COVERAGE_LABELS[int(row[s])],
                "start": index_day(int(s)).isoformat(),
                "end": index_day(int(e) - 1).isoformat(),
                "days": int(e - s),
            }
            for s, e in zip(starts, ends, strict=True)
        ]


class StoreNotReadyError(RuntimeError):
    """Raised when no data has been ingested yet."""


class Store:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._lock = threading.RLock()
        self._local = threading.local()
        self._con: duckdb.DuckDBPyConnection | None = None
        self._generation = 0
        self.revision = -1
        self.manifest: dict[str, Any] = {}
        self._coverage: dict[str | None, Coverage] | None = None
        self.refresh()

    # ---------------------------------------------------------------- lifecycle

    def refresh(self, force: bool = False) -> bool:
        """Reload the manifest and rebuild views when the store revision has changed."""
        with self._lock:
            manifest = manifest_for(self.settings).load()
            revision = int(manifest.get("revision", 0))
            if not force and revision == self.revision and self._con is not None:
                return False
            countries = self.settings.country_list
            through = archive_through(manifest, countries)
            archive_files = []
            for entry in manifest["archive"].values():
                if entry.get("status") != "ok" or entry.get("country") not in countries:
                    continue
                path = archive_path(self.settings, entry["dataset"], entry["country"], int(entry["year"]))
                if path.exists():
                    archive_files.append(path.as_posix())
            nrt_files = []
            for key in manifest["nrt"]:
                platform_key, day_text = key.split("/")
                platform = firms.PLATFORM_BY_KEY.get(platform_key)
                if platform is None:
                    continue
                day = dt.date.fromisoformat(day_text)
                horizon = through.get(platform.id)
                if horizon is not None and day <= horizon:
                    continue
                path = nrt_path(self.settings, platform_key, day)
                if path.exists():
                    nrt_files.append(path.as_posix())

            con = duckdb.connect(":memory:")
            con.execute("SET threads TO 4")
            con.execute("SET preserve_insertion_order = false")
            con.execute(f"CREATE TABLE nrt ({_EMPTY_SCHEMA})")
            if nrt_files:
                con.execute(
                    f"INSERT INTO nrt SELECT {_SELECT_COLUMNS} FROM read_parquet(?, union_by_name = true)",
                    [sorted(nrt_files)],
                )
            if archive_files:
                file_list = ", ".join("'" + f.replace("'", "''") + "'" for f in sorted(archive_files))
                con.execute(
                    f"CREATE VIEW det AS SELECT {_SELECT_COLUMNS} "
                    f"FROM read_parquet([{file_list}], union_by_name = true) "
                    f"UNION ALL SELECT {_SELECT_COLUMNS} FROM nrt"
                )
            else:
                con.execute(f"CREATE VIEW det AS SELECT {_SELECT_COLUMNS} FROM nrt")

            # The previous connection is deliberately not closed here. Request threads may still be
            # running queries on cursors derived from it, and closing it under them corrupts the
            # native heap. Dropping the reference lets it be released once those cursors are gone.
            self._con = con
            self._generation += 1
            self.manifest = manifest
            self.revision = revision
            self._archive_through = through
            self._n_archive_files = len(archive_files)
            self._n_nrt_files = len(nrt_files)
            self._coverage = None
            log.info(
                "Store revision %d loaded: %d archive files, %d near real time day files",
                revision,
                len(archive_files),
                len(nrt_files),
            )
            return True

    def cursor(self) -> duckdb.DuckDBPyConnection:
        """Thread local cursor bound to the current connection generation."""
        with self._lock:
            if self._con is None:
                raise StoreNotReadyError("Store is not initialized")
            if getattr(self._local, "generation", None) != self._generation:
                # Cursors are created under the lock: a connection must not be used from two
                # threads at once, and cursor creation is a use of the parent connection.
                self._local.cursor = self._con.cursor()
                self._local.generation = self._generation
            return self._local.cursor

    @property
    def has_data(self) -> bool:
        return self._n_archive_files > 0 or self._n_nrt_files > 0

    def require_data(self) -> None:
        if not self.has_data:
            raise StoreNotReadyError("No fire data has been ingested yet. Run: python -m agnisetu.cli ingest-archive")

    # ---------------------------------------------------------------- coverage

    def coverage(self, region: str | None = None) -> Coverage:
        """Coverage for one FIRMS region, or for the whole domain when no region is given.

        Regions are judged on their own: a sensor can be silent over one continent on a day it
        reports from another, and a missing feed for one region must not hide the data of the
        other. The domain view is the weakest state across regions, so "observed" there means
        observed everywhere.
        """
        with self._lock:
            coverages = self._coverage
        if coverages is None:
            self.require_data()
            coverages = self._build_coverages()
            with self._lock:
                self._coverage = coverages
        if region not in coverages:
            raise KeyError(f"Region '{region}' is not part of the configured domain")
        return coverages[region]

    def coverage_for(self, geometry: BaseGeometry) -> Coverage:
        """Coverage of the region an area lies in."""
        return self.coverage(home_region(geometry, self.settings.country_list))

    def _region_status(self, countries: list[str], region: str, n_days: int) -> np.ndarray:
        manifest = self.manifest
        status = np.full((firms.N_PLATFORMS, n_days), NOT_INGESTED, dtype=np.uint8)
        for platform in firms.PLATFORMS:
            status[platform.id, : max(0, day_index(platform.start))] = PRE_LAUNCH

        # Yearly archive: a year counts only when every country of the region has it.
        for ds in firms.ARCHIVE_DATASETS.values():
            years_by_country = []
            for country in countries:
                years_by_country.append(
                    {
                        int(e["year"])
                        for e in manifest["archive"].values()
                        if e.get("status") == "ok" and e.get("dataset") == ds.key and e.get("country") == country
                    }
                )
            common = set.intersection(*years_by_country) if years_by_country else set()
            for year in common:
                lo = max(0, day_index(dt.date(year, 1, 1)))
                hi = min(n_days, day_index(dt.date(year, 12, 31)) + 1)
                for platform_id in ds.platform_ids:
                    start = max(lo, day_index(firms.PLATFORM_BY_ID[platform_id].start))
                    if hi > start:
                        status[platform_id, start:hi] = OBSERVED

        # Day files: a day counts for a region once that region is stored in the file.
        for key, entry in manifest["nrt"].items():
            platform_key, day_text = key.split("/")
            platform = firms.PLATFORM_BY_KEY.get(platform_key)
            if platform is None:
                continue
            day = dt.date.fromisoformat(day_text)
            horizon = self._archive_through.get(platform.id)
            if horizon is not None and day <= horizon:
                continue
            state = day_state(entry, [region])
            idx = day_index(day)
            if state is not None and 0 <= idx < n_days:
                status[platform.id, idx] = OBSERVED if state == "complete" else PROVISIONAL
        return status

    def _build_coverages(self) -> dict[str | None, Coverage]:
        regions = firms.regions_of(self.settings.country_list)
        names = list(regions)
        today = dt.datetime.now(dt.UTC).date()
        n_days = day_index(today) + 2

        # Daily counts per platform and region. Regions are far apart, so a padded box around
        # each one assigns every detection; anything outside all boxes only joins the total.
        boxes = region_geometries(self.settings.country_list)
        cases = []
        for index, name in enumerate(names):
            west, south, east, north = boxes[name].bounds
            cases.append(
                f"WHEN lon BETWEEN {west - 1.0:.4f} AND {east + 1.0:.4f} "
                f"AND lat BETWEEN {south - 1.0:.4f} AND {north + 1.0:.4f} THEN {index}"
            )
        rows = (
            self.cursor()
            .execute(
                f"SELECT platform, CAST(date_diff('day', {EPOCH_SQL}, ldate) AS INTEGER) AS d, "
                f"CASE {' '.join(cases)} ELSE -1 END AS r, count(*) AS n FROM det GROUP BY 1, 2, 3"
            )
            .fetchnumpy()
        )
        p = np.asarray(rows["platform"], dtype=np.int64)
        d = np.asarray(rows["d"], dtype=np.int64)
        r = np.asarray(rows["r"], dtype=np.int64)
        n = np.asarray(rows["n"], dtype=np.int64)
        ok = (d >= 0) & (d < n_days)
        total = np.zeros((firms.N_PLATFORMS, n_days), dtype=np.int64)
        np.add.at(total, (p[ok], d[ok]), n[ok])
        counts = np.zeros((len(names), firms.N_PLATFORMS, n_days), dtype=np.int64)
        inside = ok & (r >= 0)
        np.add.at(counts, (r[inside], p[inside], d[inside]), n[inside])

        axis_dates = np.datetime64(EPOCH.isoformat(), "D") + np.arange(n_days)
        axis_years = axis_dates.astype("datetime64[Y]")
        doy = (axis_dates - axis_years.astype("datetime64[D]")).astype(np.int64)
        year_index = axis_years.astype(np.int64) - int(axis_years[0].astype(np.int64))
        statuses = []
        for index, name in enumerate(names):
            status = self._region_status(regions[name], name, n_days)
            for platform in firms.PLATFORMS:
                self._mark_outages(status[platform.id], counts[index, platform.id], doy, year_index)
            statuses.append(status)

        # Domain view: the weakest state across regions.
        strength = np.zeros(5, dtype=np.uint8)
        strength[[PRE_LAUNCH, NOT_INGESTED, OUTAGE, PROVISIONAL, OBSERVED]] = [0, 1, 2, 3, 4]
        code = np.array([PRE_LAUNCH, NOT_INGESTED, OUTAGE, PROVISIONAL, OBSERVED], dtype=np.uint8)
        combined = code[np.min(np.stack([strength[status] for status in statuses]), axis=0)]

        # One time axis for every view, so cubes and series built for any area line up.
        covered = np.flatnonzero(np.any(np.stack(statuses) >= OBSERVED, axis=(0, 1)))
        n_keep = (int(covered[-1]) if covered.size else 0) + 1

        def view(status: np.ndarray, day_counts: np.ndarray) -> Coverage:
            covered_any = np.flatnonzero(np.any(status[:, :n_keep] >= OBSERVED, axis=0))
            complete_any = np.flatnonzero(np.any(status[:, :n_keep] == OBSERVED, axis=0))
            last_idx = int(covered_any[-1]) if covered_any.size else 0
            last_complete_idx = int(complete_any[-1]) if complete_any.size else last_idx
            return Coverage(
                n_days=n_keep,
                status=status[:, :n_keep].copy(),
                domain_counts=day_counts[:, :n_keep].copy(),
                last_day=index_day(last_idx),
                last_complete_day=index_day(last_complete_idx),
            )

        coverages: dict[str | None, Coverage] = {None: view(combined, total)}
        for index, name in enumerate(names):
            coverages[name] = view(statuses[index], counts[index])
        return coverages

    @staticmethod
    def _mark_outages(status: np.ndarray, counts: np.ndarray, doy: np.ndarray, year_index: np.ndarray) -> None:
        """Turn implausible silent runs into OUTAGE so they are never read as zero fire days.

        `doy` is the zero based day of year and `year_index` the zero based year of every day.
        The expectation for a calendar day is the median, across years and a few neighbouring
        days, of the daily count on that day of year. A quiet season therefore explains its own zeros, while a
        silent sensor in a season that is normally busy does not.
        """
        observed = status == OBSERVED
        zero = observed & (counts == 0)
        if not np.any(zero):
            return
        table = np.full((int(year_index.max()) + 1, 366), np.nan)
        table[year_index[observed], doy[observed]] = counts[observed]
        years_available = np.sum(np.isfinite(table), axis=0)
        # Pool a few neighbouring days of year with a median, not a mean, so a sharp season
        # edge does not leak expectation into the quiet days next to it.
        half = OUTAGE_POOL_DAYS // 2
        pooled = np.concatenate([np.roll(table, shift, axis=1) for shift in range(-half, half + 1)], axis=0)
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            climatology = np.nanmedian(pooled, axis=0)
        climatology = np.where(years_available >= OUTAGE_MIN_YEARS, np.nan_to_num(climatology, nan=0.0), 0.0)
        expected = climatology[doy]

        idx = np.flatnonzero(zero)
        breaks = np.flatnonzero(np.diff(idx) > 1)
        run_starts = np.concatenate(([idx[0]], idx[breaks + 1]))
        run_ends = np.concatenate((idx[breaks], [idx[-1]]))
        for start, end in zip(run_starts, run_ends, strict=True):
            window = expected[start : end + 1]
            if float(window.sum()) < OUTAGE_EXPECTED_MIN:
                continue
            # Quiet days at either end of the run are plausible zeros and stay observed.
            busy = np.flatnonzero(window >= 1.0)
            status[start + busy[0] : start + busy[-1] + 1] = OUTAGE

    # ---------------------------------------------------------------- summaries

    def coverage_summary(self) -> dict[str, Any]:
        if not self.has_data:
            return {"ready": False, "revision": self.revision, "platforms": [], "countries": self.settings.country_list}
        cov = self.coverage()
        platforms = []
        for platform in firms.PLATFORMS:
            row = cov.status[platform.id]
            observed = np.flatnonzero(row == OBSERVED)
            any_cov = np.flatnonzero(row >= OBSERVED)
            platforms.append(
                {
                    "id": platform.id,
                    "key": platform.key,
                    "label": platform.label,
                    "instrument": platform.instrument,
                    "resolution_m": platform.resolution_m,
                    "overpass_local": platform.overpass_local,
                    "record_start": platform.start.isoformat(),
                    "first_observed": index_day(int(observed[0])).isoformat() if observed.size else None,
                    "last_observed": index_day(int(observed[-1])).isoformat() if observed.size else None,
                    "last_any": index_day(int(any_cov[-1])).isoformat() if any_cov.size else None,
                    "days_observed": int(observed.size),
                    "days_outage": int(np.sum(row == OUTAGE)),
                    "days_provisional": int(np.sum(row == PROVISIONAL)),
                    "detections": int(cov.domain_counts[platform.id].sum()),
                    "archive_through": (
                        self._archive_through[platform.id].isoformat() if platform.id in self._archive_through else None
                    ),
                }
            )
        return {
            "ready": True,
            "revision": self.revision,
            "countries": self.settings.country_list,
            "record_start": EPOCH.isoformat(),
            "last_day": cov.last_day.isoformat(),
            "last_complete_day": cov.last_complete_day.isoformat(),
            "platforms": platforms,
            "static_mask": self.manifest.get("static_mask"),
            "updated_at": self.manifest.get("updated_at"),
        }


_store: Store | None = None
_store_lock = threading.Lock()


def get_store() -> Store:
    global _store
    with _store_lock:
        if _store is None:
            _store = Store()
        return _store


def reset_store() -> None:
    """Drop the singleton. Used by tests that point the settings at a temporary store."""
    global _store
    with _store_lock:
        _store = None
