"""Analysis orchestration: one cached computation per area and option set, many views of it."""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import numpy as np
import shapely

from . import anomaly, boundaries, burncal, firms, harmonize, warning
from .aoi import Aoi, AoiError, build_aoi, parse_geojson
from .config import Settings
from .cube import Cube, CubeBuilder
from .stats import midrank_percentile, nan_to_none, quantiles, to_list
from .store import EPOCH, OBSERVED, Coverage, Store
from .timeaxis import N_BINS_PER_YEAR, TimeAxis, bin_dates, bin_label, make_axis

log = logging.getLogger("agnisetu.analysis")

METRICS = {
    "count": {
        "key": "count",
        "label": "Active fire detections",
        "unit": "VIIRS S-NPP equivalent detections",
        "unit_short": "detections",
    },
    "frp": {
        "key": "frp",
        "label": "Fire radiative power",
        "unit": "megawatts, VIIRS S-NPP equivalent",
        "unit_short": "MW",
    },
}
CONFIDENCE = {
    "all": {"key": "all", "label": "All detections", "min_conf": 0},
    "nominal": {"key": "nominal", "label": "Nominal and high confidence", "min_conf": 1},
    "high": {"key": "high", "label": "High confidence only", "min_conf": 2},
}
SERIES_KINDS = ("harmonized", "modis", "snpp", "noaa20")
ERA_MARKERS = (
    {"date": "2000-11-01", "label": "MODIS Terra record begins"},
    {"date": "2002-07-04", "label": "MODIS Aqua added"},
    {"date": "2012-01-20", "label": "VIIRS S-NPP begins (reference sensor)"},
    {"date": "2018-04-01", "label": "VIIRS NOAA-20 added"},
)


class NotFoundError(LookupError):
    """Unknown preset or period."""


@dataclass(frozen=True)
class Options:
    metric: str = "count"
    confidence: str = "nominal"

    @property
    def min_conf(self) -> int:
        return int(CONFIDENCE[self.confidence]["min_conf"])

    def to_json(self) -> dict[str, Any]:
        return {"metric": self.metric, "confidence": self.confidence}


@dataclass
class Base:
    """Everything that depends only on the area, the store revision and the options."""

    aoi: Aoi
    options: Options
    axis: TimeAxis
    coverage: Coverage
    cube: Cube
    x: np.ndarray  # [platform, day] metric values
    observed: np.ndarray  # [platform, day] bool
    fits: dict[str, harmonize.SetFit]
    unavailable: dict[str, str]
    series: harmonize.HarmonizedSeries
    raw_daily: dict[str, np.ndarray]
    weekly: np.ndarray  # [bin] harmonized activity per 7 days
    weekly_lower: np.ndarray
    weekly_upper: np.ndarray
    weekly_days: np.ndarray
    matrix: np.ndarray  # [year, 52]
    seconds: float

    @property
    def is_estimate(self) -> np.ndarray:
        return self.series.vtype >= harmonize.VT_VIIRS_CALIBRATED


@dataclass
class Seasonal:
    """Baseline dependent products."""

    baseline: tuple[int, int]
    baseline_mask: np.ndarray
    clim: burncal.Climatology
    periods: list[burncal.CriticalPeriod]
    stats: dict[str, burncal.SeasonStats]


class _Lru:
    def __init__(self, size: int) -> None:
        self.size = size
        self._data: OrderedDict[Any, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any | None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
            return None

    def put(self, key: Any, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.size:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class AnalysisService:
    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self.cubes = CubeBuilder(store, settings.cache_dir, memory_entries=settings.aoi_cache_entries)
        self._bases = _Lru(settings.aoi_cache_entries)
        self._seasonal = _Lru(settings.aoi_cache_entries * 2)
        self._domain_fraction = _Lru(256)
        self._heavy = threading.BoundedSemaphore(settings.max_concurrent_heavy)
        self._inflight: dict[Any, threading.Event] = {}
        self._inflight_lock = threading.Lock()

    # ------------------------------------------------------------------ areas

    def resolve_aoi(self, preset_id: str | None, geometry: dict[str, Any] | None, name: str | None) -> Aoi:
        limits = {
            "max_vertices": self.settings.max_aoi_vertices,
            "min_area_km2": self.settings.min_aoi_area_km2,
            "max_area_km2": self.settings.max_aoi_area_km2,
        }
        if preset_id:
            try:
                entry = boundaries.get_preset(preset_id)
                geom = boundaries.preset_geometry(preset_id)
            except KeyError as exc:
                raise NotFoundError(f"Unknown preset area '{preset_id[:80]}'") from exc
            # Presets are trusted reference data; only the upper size limits apply to them.
            return build_aoi(
                geom, entry["name"], "preset", preset_id, max_vertices=10**7, min_area_km2=0.0, max_area_km2=1e9
            )
        if geometry is None:
            raise AoiError("Provide either a preset id or a GeoJSON geometry")
        return parse_geojson(geometry, name or "Custom area", **limits)

    def domain_fraction(self, aoi: Aoi) -> float:
        cached = self._domain_fraction.get(aoi.id)
        if cached is not None:
            return cached
        domain = boundaries.domain_geometry(self.settings.country_list)
        try:
            inside = shapely.intersection(aoi.geometry, domain).area
            fraction = float(inside / aoi.geometry.area) if aoi.geometry.area > 0 else 0.0
        except shapely.errors.GEOSException:
            fraction = 0.0
        fraction = round(min(1.0, max(0.0, fraction)), 4)
        self._domain_fraction.put(aoi.id, fraction)
        return fraction

    def aoi_json(self, aoi: Aoi) -> dict[str, Any]:
        return {
            "id": aoi.id,
            "name": aoi.name,
            "source": aoi.source,
            "preset_id": aoi.preset_id,
            "area_km2": round(aoi.area_km2, 1),
            "bbox": [round(v, 5) for v in aoi.bbox],
            "vertices": aoi.vertices,
            "is_rectangle": aoi.is_rectangle,
            "repaired": aoi.repaired,
            "domain_fraction": self.domain_fraction(aoi),
            "domain_countries": [firms.get_country(c).label for c in self.settings.country_list],
        }

    # ------------------------------------------------------------------ base

    def invalidate(self) -> None:
        self._bases.clear()
        self._seasonal.clear()
        self.cubes.purge_stale()

    def base(self, aoi: Aoi, options: Options) -> Base:
        self.store.require_data()
        key = (aoi.id, self.store.revision, options.metric, options.confidence)
        while True:
            cached = self._bases.get(key)
            if cached is not None:
                return cached
            with self._inflight_lock:
                waiter = self._inflight.get(key)
                if waiter is None:
                    self._inflight[key] = threading.Event()
                    break
            waiter.wait(timeout=300)
        try:
            with self._heavy:
                base = self._build_base(aoi, options)
            self._bases.put(key, base)
            return base
        finally:
            with self._inflight_lock:
                event = self._inflight.pop(key, None)
            if event is not None:
                event.set()

    def _build_base(self, aoi: Aoi, options: Options) -> Base:
        started = time.perf_counter()
        coverage = self.store.coverage_for(aoi.geometry)
        axis = make_axis(coverage.n_days)
        cube = self.cubes.get(aoi)
        x = cube.series(options.metric, options.min_conf)
        observed = coverage.status == OBSERVED

        def scope_provider(scope: str) -> tuple[np.ndarray, np.ndarray] | None:
            if scope == "area":
                scoped = cube
            else:
                west, south, east, north = aoi.bbox
                if scope == "domain":
                    bbox = boundaries.home_region_bounds(aoi.geometry, self.settings.country_list)
                else:
                    pad = 1.0 if scope == "buffer_1deg" else 3.0
                    bbox = (
                        max(-180.0, west - pad),
                        max(-90.0, south - pad),
                        min(180.0, east + pad),
                        min(90.0, north + pad),
                    )
                scoped = self.cubes.get_rect(bbox)
            return scoped.series(options.metric, options.min_conf), scoped.count_series(options.min_conf)

        fits, unavailable = harmonize.fit_all_sets(axis, observed, scope_provider)
        series = harmonize.assemble(axis, x, observed, fits)

        modis_cov = observed[firms.TERRA] | observed[firms.AQUA]
        raw_daily = {
            "modis": np.where(
                modis_cov,
                np.where(observed[firms.TERRA], x[firms.TERRA], 0.0)
                + np.where(observed[firms.AQUA], x[firms.AQUA], 0.0),
                np.nan,
            ),
            "snpp": np.where(observed[firms.SNPP], x[firms.SNPP], np.nan),
            "noaa20": np.where(observed[firms.NOAA20], x[firms.NOAA20], np.nan),
        }
        weekly, days = burncal.daily_to_weekly(axis, series.value)
        weekly_lower, _ = burncal.daily_to_weekly(axis, series.lower)
        weekly_upper, _ = burncal.daily_to_weekly(axis, series.upper)
        base = Base(
            aoi=aoi,
            options=options,
            axis=axis,
            coverage=coverage,
            cube=cube,
            x=x,
            observed=observed,
            fits=fits,
            unavailable=unavailable,
            series=series,
            raw_daily=raw_daily,
            weekly=weekly,
            weekly_lower=weekly_lower,
            weekly_upper=weekly_upper,
            weekly_days=days,
            matrix=burncal.to_matrix(axis, weekly),
            seconds=round(time.perf_counter() - started, 3),
        )
        log.info(
            "Analysis base for %s (%s, %s) built in %.2fs", aoi.id, options.metric, options.confidence, base.seconds
        )
        return base

    def seasonal(self, base: Base, baseline: tuple[int, int] | None) -> Seasonal:
        years = base.axis.years
        complete = burncal.complete_years(base.matrix)
        complete_years_list = [y for y, c in zip(years, complete, strict=True) if c]
        if not complete_years_list:
            lo, hi = years[0], years[-1]
        else:
            lo, hi = complete_years_list[0], complete_years_list[-1]
        if baseline is not None:
            lo = max(lo, min(baseline))
            hi = min(hi, max(baseline))
            if hi - lo + 1 < 5:
                raise ValueError("The baseline period must span at least 5 complete years")
        key = (base.aoi.id, self.store.revision, base.options.metric, base.options.confidence, lo, hi)
        cached = self._seasonal.get(key)
        if cached is not None:
            return cached
        clim = burncal.climatology(base.matrix, years, (lo, hi))
        periods = burncal.detect_critical_periods(clim.mean)
        mask = np.array([lo <= y <= hi for y in years]) & complete
        stats = {
            p.id: burncal.season_stats(
                base.axis, p, base.series.value, base.series.lower, base.series.upper, base.is_estimate
            )
            for p in periods
        }
        seasonal = Seasonal((lo, hi), mask, clim, periods, stats)
        self._seasonal.put(key, seasonal)
        return seasonal

    # ------------------------------------------------------------------ views

    def _envelope(self, base: Base) -> dict[str, Any]:
        return {
            "aoi": self.aoi_json(base.aoi),
            "options": base.options.to_json(),
            "metric": METRICS[base.options.metric],
            "confidence": {k: v for k, v in CONFIDENCE[base.options.confidence].items() if k != "min_conf"},
            "record": {
                "start": EPOCH.isoformat(),
                "end": base.coverage.last_day.isoformat(),
                "last_complete_day": base.coverage.last_complete_day.isoformat(),
            },
            "store_revision": self.store.revision,
        }

    def summary(self, aoi: Aoi, options: Options) -> dict[str, Any]:
        base = self.base(aoi, options)
        axis = base.axis
        cube = base.cube
        counts = cube.totals_by_platform(options.min_conf)
        frp_totals = cube.frp[:, :, options.min_conf :, :].sum(axis=(0, 2, 3), dtype=np.float64)
        platforms = []
        for platform in firms.PLATFORMS:
            row = cube.counts[:, platform.id, options.min_conf :, :].sum(axis=(1, 2))
            nz = np.flatnonzero(row)
            platforms.append(
                {
                    "key": platform.key,
                    "label": platform.label,
                    "instrument": platform.instrument,
                    "detections": int(counts[platform.id]),
                    "frp_mw": round(float(frp_totals[platform.id]), 1),
                    "static_removed": int(cube.static_counts[platform.id]),
                    "first_detection": axis.date(int(nz[0])).isoformat() if nz.size else None,
                    "last_detection": axis.date(int(nz[-1])).isoformat() if nz.size else None,
                }
            )
        totals, _ = burncal.annual_totals(axis, base.series.value)
        finite = np.isfinite(totals)
        years = np.asarray(axis.years)
        annual = {
            "mean": nan_to_none(float(np.mean(totals[finite])) if finite.any() else None, 1),
            "median": nan_to_none(float(np.median(totals[finite])) if finite.any() else None, 1),
            "peak_year": int(years[finite][np.argmax(totals[finite])]) if finite.any() else None,
            "peak_value": nan_to_none(float(np.max(totals[finite])) if finite.any() else None, 1),
            "lowest_year": int(years[finite][np.argmin(totals[finite])]) if finite.any() else None,
            "lowest_value": nan_to_none(float(np.min(totals[finite])) if finite.any() else None, 1),
            "complete_years": int(finite.sum()),
        }
        all_detections = int(cube.counts.sum())
        static_total = int(cube.static_counts.sum())
        last_idx = axis.index(base.coverage.last_complete_day)
        sums = warning.WindowSums(base.series.value)
        return {
            **self._envelope(base),
            "platforms": platforms,
            "detections_total": int(counts.sum()),
            "detections_all_confidence": all_detections,
            "static_removed_total": static_total,
            "static_share": round(static_total / (static_total + all_detections), 4)
            if static_total + all_detections
            else 0.0,
            "harmonized_annual": annual,
            "days_by_type": base.series.counts_by_type(),
            "latest": {
                "date": base.coverage.last_complete_day.isoformat(),
                "last_7_days": nan_to_none(sums.total(last_idx, 7), 1),
                "last_30_days": nan_to_none(sums.total(last_idx, 30), 1),
            },
            "timing": {"cube_seconds": cube.build_seconds, "analysis_seconds": base.seconds},
        }

    def calendar(
        self, aoi: Aoi, options: Options, baseline: tuple[int, int] | None, series_kind: str, resolution: str
    ) -> dict[str, Any]:
        base = self.base(aoi, options)
        seasonal = self.seasonal(base, baseline)
        axis = base.axis
        daily = base.series.value if series_kind == "harmonized" else base.raw_daily[series_kind]
        out: dict[str, Any] = {
            **self._envelope(base),
            "series": series_kind,
            "resolution": resolution,
            "years": axis.years,
            "value_types": {str(k): v for k, v in harmonize.VALUE_TYPES.items()},
        }
        if resolution == "monthly":
            matrix = self._monthly_view(base, series_kind, daily, seasonal, out)
            values = matrix[np.isfinite(matrix)]
        elif resolution == "daily":
            matrix = burncal.daily_matrix(axis, daily)
            out["matrix"] = to_list(matrix, 1)
            if series_kind == "harmonized":
                vt = np.zeros((axis.n_years, 366), dtype=np.int8)
                vt[axis.year - axis.first_year, axis.doy - 1] = base.series.vtype
                out["vtype"] = vt.tolist()
            values = matrix[np.isfinite(matrix)]
        else:
            if series_kind == "harmonized":
                weekly, lower, upper, days = base.weekly, base.weekly_lower, base.weekly_upper, base.weekly_days
                out["lower"] = to_list(burncal.to_matrix(axis, lower), 1)
                out["upper"] = to_list(burncal.to_matrix(axis, upper), 1)
                vt_counts = np.zeros((axis.n_bins, len(harmonize.VALUE_TYPES)), dtype=np.int64)
                np.add.at(vt_counts, (axis.bin_id, base.series.vtype), 1)
                vt_counts[:, harmonize.VT_MISSING] = 0
                dominant = np.where(vt_counts.sum(axis=1) > 0, np.argmax(vt_counts, axis=1), harmonize.VT_MISSING)
                dominant = np.where(np.isfinite(weekly), dominant, harmonize.VT_MISSING)
                out["vtype"] = burncal.to_matrix(axis, dominant).astype(int).tolist()
            else:
                weekly, days = burncal.daily_to_weekly(axis, daily)
            matrix = burncal.to_matrix(axis, weekly)
            out["matrix"] = to_list(matrix, 1)
            out["days_covered"] = burncal.to_matrix(axis, days).astype(int).tolist()
            values = matrix[np.isfinite(matrix)]
        out["scale_max"] = nan_to_none(float(np.quantile(values, 0.995)) if values.size else 0.0, 1)
        totals, fraction = burncal.annual_totals(axis, daily)
        out["annual"] = {
            "total": to_list(totals, 1),
            "days_fraction": to_list(fraction, 3),
        }
        out["weeks"] = [{"index": w, "label": bin_label(w), "start_doy": 7 * w + 1} for w in range(N_BINS_PER_YEAR)]
        out["baseline"] = {
            "start": seasonal.baseline[0],
            "end": seasonal.baseline[1],
            "years_used": seasonal.clim.years_used,
        }
        out["climatology"] = seasonal.clim.to_json()
        out["critical_periods"] = [burncal.period_summary(p, seasonal.stats[p.id]) for p in seasonal.periods]
        out["era_markers"] = list(ERA_MARKERS)
        return out

    def _monthly_view(
        self, base: Base, series_kind: str, daily: np.ndarray, seasonal: Seasonal, out: dict[str, Any]
    ) -> np.ndarray:
        """Year by month totals, the monthly climatology and the highest months and years."""
        axis = base.axis
        matrix, days, _ = burncal.monthly_totals(axis, daily)
        out["matrix"] = to_list(matrix, 1)
        out["days_covered"] = days.astype(int).tolist()
        if series_kind == "harmonized":
            lower, _, _ = burncal.monthly_totals(axis, base.series.lower)
            upper, _, _ = burncal.monthly_totals(axis, base.series.upper)
            out["lower"] = to_list(lower, 1)
            out["upper"] = to_list(upper, 1)
            counts = np.zeros((axis.n_years * 12, len(harmonize.VALUE_TYPES)), dtype=np.int64)
            np.add.at(counts, (burncal.month_bins(axis), base.series.vtype), 1)
            counts[:, harmonize.VT_MISSING] = 0
            dominant = np.where(counts.sum(axis=1) > 0, np.argmax(counts, axis=1), harmonize.VT_MISSING)
            dominant = np.where(np.isfinite(matrix.ravel()), dominant, harmonize.VT_MISSING)
            out["vtype"] = dominant.reshape(axis.n_years, 12).astype(int).tolist()

        reference = matrix[seasonal.baseline_mask]
        if reference.shape[0] == 0:
            reference = matrix
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            q = np.nanquantile(reference, (0.10, 0.25, 0.50, 0.75, 0.90), axis=0)
            mean = np.nanmean(reference, axis=0)
            highest = np.nanmax(reference, axis=0)
        out["months"] = [{"index": i, "label": name, "short": name[:3]} for i, name in enumerate(burncal.MONTHS)]
        out["monthly_climatology"] = {
            "p10": to_list(q[0], 1),
            "p25": to_list(q[1], 1),
            "median": to_list(q[2], 1),
            "p75": to_list(q[3], 1),
            "p90": to_list(q[4], 1),
            "mean": to_list(mean, 1),
            "max": to_list(highest, 1),
            "n_years": np.sum(np.isfinite(reference), axis=0).astype(int).tolist(),
        }
        median = q[2]
        cells = [
            (float(matrix[y, m]), y, m) for y in range(axis.n_years) for m in range(12) if np.isfinite(matrix[y, m])
        ]
        cells.sort(reverse=True)
        out["top_months"] = [
            {
                "year": axis.years[y],
                "month": m + 1,
                "label": f"{burncal.MONTHS[m]} {axis.years[y]}",
                "value": nan_to_none(value, 1),
                "ratio_to_median": nan_to_none(
                    value / median[m] if np.isfinite(median[m]) and median[m] > 0 else None, 2
                ),
            }
            for value, y, m in cells[:10]
        ]
        totals, _ = burncal.annual_totals(axis, daily)
        finite = [(float(t), axis.years[i]) for i, t in enumerate(totals) if np.isfinite(t)]
        finite.sort(reverse=True)
        typical = float(np.median([t for t, _ in finite])) if finite else float("nan")
        out["top_years"] = [
            {
                "year": year,
                "value": nan_to_none(total, 1),
                "ratio_to_median": nan_to_none(total / typical if typical > 0 else None, 2),
            }
            for total, year in finite[:5]
        ]
        return matrix

    def range_report(self, aoi: Aoi, options: Options, date_from: dt.date, date_to: dt.date) -> dict[str, Any]:
        """Harmonized activity for one date range, with the same calendar dates in other years."""
        base = self.base(aoi, options)
        seasonal = self.seasonal(base, None)
        axis = base.axis
        first_day, last_day = EPOCH, base.coverage.last_day
        if date_to < date_from:
            raise ValueError("date_to must not be before date_from")
        if date_from < first_day or date_to > last_day:
            raise ValueError(f"The date range must lie between {first_day.isoformat()} and {last_day.isoformat()}")
        lo, hi = axis.index(date_from), axis.index(date_to) + 1
        n = hi - lo
        value, lower, upper = base.series.value[lo:hi], base.series.lower[lo:hi], base.series.upper[lo:hi]
        vtype = base.series.vtype[lo:hi]
        finite = np.isfinite(value)
        have = int(finite.sum())
        enough = have >= burncal.MIN_WINDOW_COVERAGE * n
        scale = n / have if have else 0.0

        def scaled(arr: np.ndarray) -> float | None:
            return nan_to_none(float(arr[finite].sum()) * scale, 1) if enough else None

        peak = None
        if have:
            i = int(np.nanargmax(np.where(finite, value, -np.inf)))
            peak = {"date": axis.date(lo + i).isoformat(), "value": nan_to_none(float(value[i]), 1)}

        platforms = []
        for platform in firms.PLATFORMS:
            observed = base.observed[platform.id, lo:hi]
            platforms.append(
                {
                    "key": platform.key,
                    "label": platform.label,
                    "days_observed": int(observed.sum()),
                    "raw_total": nan_to_none(float(base.x[platform.id, lo:hi][observed].sum()), 1),
                }
            )

        comparison: dict[str, Any] = {"available": False}
        if n > 366:
            comparison["reason"] = "Comparison with other years is offered for ranges of up to one year."
        elif not enough:
            comparison["reason"] = "Fewer than 80 percent of the days in this range have data."
        else:
            sums = warning.WindowSums(base.series.value)
            span_years = sorted({date_from.year, date_to.year})
            history = []
            for year in axis.years:
                shift = year - date_to.year
                if shift == 0:
                    continue
                end = warning.same_day(date_to.year + shift, date_to.month, date_to.day)
                total = sums.total(axis.index(end), n)
                if np.isfinite(total):
                    history.append({"year": end.year, "value": nan_to_none(total, 1)})
            reference = np.array([h["value"] for h in history], dtype=float)
            current = float(value[finite].sum()) * scale
            if reference.size >= 5:
                q = quantiles(reference, (0.10, 0.25, 0.50, 0.75, 0.90))
                mean = float(reference.mean())
                comparison = {
                    "available": True,
                    "reference_years": int(reference.size),
                    "p10": nan_to_none(q[0], 1),
                    "p25": nan_to_none(q[1], 1),
                    "median": nan_to_none(q[2], 1),
                    "p75": nan_to_none(q[3], 1),
                    "p90": nan_to_none(q[4], 1),
                    "percentile": nan_to_none(midrank_percentile(current, reference), 1),
                    "ratio_to_median": nan_to_none(current / q[2] if q[2] > 0 else None, 2),
                    "mean": nan_to_none(mean, 1),
                    "anomaly_vs_mean": nan_to_none((current - mean) / mean if mean > 0 else None, 3),
                    "rank": int(np.sum(reference > current)) + 1,
                    "history": history,
                    "year_of_range": span_years[-1],
                }
            else:
                comparison["reason"] = "Too few other years have data for these calendar dates."

        if n <= 124:
            resolution = "daily"
            labels = [axis.date(i).isoformat() for i in range(lo, hi)]
            series_value, series_lower, series_upper = value, lower, upper
            series_type = vtype.astype(int).tolist()
            typical = seasonal.clim.median[axis.week[lo:hi]] / 7.0
        else:
            step = 7 if n <= 3 * 366 else 30
            resolution = "weekly" if step == 7 else "thirty_day"
            starts = list(range(lo, hi, step))
            labels = [axis.date(s).isoformat() for s in starts]
            sums_v, sums_l, sums_u, typical_list, types = [], [], [], [], []
            for s in starts:
                e = min(s + step, hi)
                ok = np.isfinite(base.series.value[s:e])
                if ok.sum() >= burncal.MIN_WINDOW_COVERAGE * (e - s):
                    k = (e - s) / ok.sum()
                    sums_v.append(float(base.series.value[s:e][ok].sum()) * k)
                    sums_l.append(float(base.series.lower[s:e][ok].sum()) * k)
                    sums_u.append(float(base.series.upper[s:e][ok].sum()) * k)
                else:
                    sums_v.append(np.nan)
                    sums_l.append(np.nan)
                    sums_u.append(np.nan)
                typical_list.append(float(np.nansum(seasonal.clim.median[axis.week[s:e]] / 7.0)))
                kinds = base.series.vtype[s:e]
                kinds = kinds[kinds != harmonize.VT_MISSING]
                types.append(int(np.bincount(kinds).argmax()) if kinds.size else harmonize.VT_MISSING)
            series_value, series_lower, series_upper = np.array(sums_v), np.array(sums_l), np.array(sums_u)
            series_type = types
            typical = np.array(typical_list)

        return {
            **self._envelope(base),
            "range": {"from": date_from.isoformat(), "to": date_to.isoformat(), "days": n},
            "bounds": {"min": first_day.isoformat(), "max": last_day.isoformat()},
            "total": scaled(value),
            "lower": scaled(lower),
            "upper": scaled(upper),
            "days_with_data": have,
            "days_missing": n - have,
            "mean_per_day": nan_to_none(float(value[finite].mean()) if have else None, 2),
            "peak_day": peak,
            "days_by_type": {harmonize.VALUE_TYPES[t]: int(np.sum(vtype == t)) for t in harmonize.VALUE_TYPES},
            "platforms": platforms,
            "comparison": comparison,
            "series": {
                "resolution": resolution,
                "labels": labels,
                "values": to_list(series_value, 1),
                "lower": to_list(series_lower, 1),
                "upper": to_list(series_upper, 1),
                "vtype": series_type,
                "typical": to_list(typical, 1),
            },
        }

    def harmonization(self, aoi: Aoi, options: Options) -> dict[str, Any]:
        base = self.base(aoi, options)
        axis = base.axis
        sets = []
        for key in harmonize.PREDICTOR_SETS:
            label = harmonize.SET_LABELS[key]
            platforms = [firms.PLATFORM_BY_ID[p].key for p in harmonize.PREDICTOR_SETS[key]]
            fit = base.fits.get(key)
            if fit is None:
                sets.append(
                    {
                        "key": key,
                        "label": label,
                        "platforms": platforms,
                        "available": False,
                        "reason": base.unavailable.get(key, "Not available"),
                    }
                )
                continue
            pairs = fit.pairs
            starts = [bin_dates(int(y), int(w))[0].isoformat() for y, w in zip(pairs.year, pairs.week, strict=True)]
            used_days = int(np.sum(base.series.source_set == harmonize.PRIORITY.index(key)))
            sets.append(
                {
                    "key": key,
                    "label": label,
                    "platforms": platforms,
                    "available": True,
                    "scope": fit.scope,
                    "scope_label": fit.scope_label,
                    "n_weeks": pairs.n,
                    "n_years": int(np.unique(pairs.year).size),
                    "overlap_start": starts[0] if starts else None,
                    "overlap_end": starts[-1] if starts else None,
                    "predictor_detections": int(pairs.x_detections),
                    "selected": fit.selected,
                    "selected_label": harmonize.MODEL_LABELS[fit.selected],
                    "candidates": fit.candidates,
                    "interval": {
                        "coverage": 0.8,
                        "log_low": [round(v, 3) for v in fit.interval.low],
                        "log_high": [round(v, 3) for v in fit.interval.high],
                        "thresholds": [round(v, 2) for v in fit.interval.thresholds],
                    },
                    "factor_curve": harmonize.effective_factor_curve(fit),
                    "days_used": used_days,
                    "scatter": {
                        "x": to_list(pairs.xs.sum(axis=1), 2),
                        "y": to_list(pairs.y, 2),
                        "week": pairs.week.tolist(),
                        "year": pairs.year.tolist(),
                    },
                    "validation": {
                        "dates": starts,
                        "observed": to_list(pairs.y, 1),
                        "predicted": to_list(fit.oof, 1),
                        "lower": to_list(np.where(np.isfinite(fit.oof), fit.oof_lower, np.nan), 1),
                        "upper": to_list(np.where(np.isfinite(fit.oof), fit.oof_upper, np.nan), 1),
                    },
                }
            )

        harmonized, _ = burncal.annual_totals(axis, base.series.value)
        lower, _ = burncal.annual_totals(axis, base.series.lower)
        upper, _ = burncal.annual_totals(axis, base.series.upper)
        modis, _ = burncal.annual_totals(axis, base.raw_daily["modis"])
        snpp, _ = burncal.annual_totals(axis, base.raw_daily["snpp"])
        noaa20, _ = burncal.annual_totals(axis, base.raw_daily["noaa20"])
        years = np.asarray(axis.years)
        naive = np.where(years >= 2012, snpp, modis)

        def step(series: np.ndarray, before_src: np.ndarray | None = None) -> float | None:
            before = (before_src if before_src is not None else series)[(years >= 2009) & (years <= 2011)]
            after = series[(years >= 2012) & (years <= 2014)]
            before = before[np.isfinite(before)]
            after = after[np.isfinite(after)]
            if before.size == 0 or after.size == 0 or before.mean() <= 0:
                return None
            return round(float(after.mean() / before.mean()), 3)

        timeline = [
            {"key": p.key, "label": p.label, "instrument": p.instrument, "segments": base.coverage.segments(p.id)}
            for p in firms.PLATFORMS
        ]
        return {
            **self._envelope(base),
            "reference": {"key": "SNPP", "label": firms.PLATFORM_BY_ID[harmonize.REFERENCE].label},
            "sets": sets,
            "annual": {
                "years": axis.years,
                "modis_raw": to_list(modis, 1),
                "snpp_raw": to_list(snpp, 1),
                "noaa20_raw": to_list(noaa20, 1),
                "naive": to_list(naive, 1),
                "harmonized": to_list(harmonized, 1),
                "harmonized_lower": to_list(lower, 1),
                "harmonized_upper": to_list(upper, 1),
            },
            "continuity": {
                "before_years": [2009, 2010, 2011],
                "after_years": [2012, 2013, 2014],
                "naive_step": step(snpp, modis),
                "harmonized_step": step(harmonized),
                # The same years seen by MODIS alone: an independent check, because one instrument
                # compared with itself has no sensor change in it.
                "modis_step": step(modis),
            },
            "days_by_type": base.series.counts_by_type(),
            "coverage_timeline": timeline,
            "priority": [
                {"key": k, "label": harmonize.SET_LABELS[k], "available": k in base.fits} for k in harmonize.PRIORITY
            ],
        }

    def _pick_period(self, seasonal: Seasonal, period_id: str | None) -> burncal.CriticalPeriod | None:
        if not seasonal.periods:
            return None
        if period_id is None:
            return max(seasonal.periods, key=lambda p: p.share)
        for period in seasonal.periods:
            if period.id == period_id:
                return period
        raise NotFoundError(f"Unknown critical period '{period_id[:20]}'")

    def anomalies(
        self, aoi: Aoi, options: Options, baseline: tuple[int, int] | None, period_id: str | None
    ) -> dict[str, Any]:
        base = self.base(aoi, options)
        seasonal = self.seasonal(base, baseline)
        period = self._pick_period(seasonal, period_id)
        percentile, classes = anomaly.weekly_anomaly_matrix(base.matrix, seasonal.baseline_mask)
        out: dict[str, Any] = {
            **self._envelope(base),
            "years": base.axis.years,
            "baseline": {
                "start": seasonal.baseline[0],
                "end": seasonal.baseline[1],
                "years_used": seasonal.clim.years_used,
            },
            "periods": [burncal.period_summary(p, seasonal.stats[p.id]) for p in seasonal.periods],
            "classes": [{"key": k, "label": anomaly.CLASS_LABELS[k]} for k in anomaly.CLASSES],
            "weekly": {
                "percentile": to_list(percentile, 0),
                "class_index": classes.astype(int).tolist(),
            },
            "weeks": [{"index": w, "label": bin_label(w)} for w in range(N_BINS_PER_YEAR)],
            "selected_period": period.id if period else None,
            "seasons": [],
            "ranking": [],
            "trends": None,
            "reference": None,
        }
        if period is None:
            return out
        stats = seasonal.stats[period.id]
        rows = anomaly.season_anomalies(stats, seasonal.baseline_mask)
        totals = stats.total[seasonal.baseline_mask & np.isfinite(stats.total)]
        q = quantiles(totals, (0.10, 0.25, 0.50, 0.75, 0.90))
        out["seasons"] = rows
        out["ranking"] = sorted(
            (r for r in rows if r.get("available") and r.get("robust_z") is not None),
            key=lambda r: abs(r["robust_z"]),
            reverse=True,
        )[:10]
        out["trends"] = anomaly.season_trends(period, stats, seasonal.baseline_mask)
        out["reference"] = {
            "p10": nan_to_none(q[0], 1),
            "p25": nan_to_none(q[1], 1),
            "median": nan_to_none(q[2], 1),
            "p75": nan_to_none(q[3], 1),
            "p90": nan_to_none(q[4], 1),
            "n_years": int(totals.size),
        }
        return out

    def as_of_bounds(self, base: Base) -> tuple[dt.date, dt.date, dt.date]:
        finite = np.flatnonzero(np.isfinite(base.series.value))
        first = base.axis.date(int(finite[0]) + 14) if finite.size else EPOCH + dt.timedelta(days=14)
        last = base.coverage.last_day
        return first, last, base.coverage.last_complete_day

    def warning(self, aoi: Aoi, options: Options, as_of: dt.date | None) -> dict[str, Any]:
        base = self.base(aoi, options)
        seasonal = self.seasonal(base, None)
        first, last, default = self.as_of_bounds(base)
        day = as_of or default
        if not first <= day <= last:
            raise ValueError(f"The as of date must be between {first.isoformat()} and {last.isoformat()}")
        result = warning.build_warning(
            base.axis,
            base.series.value,
            base.series.vtype,
            seasonal.periods,
            seasonal.stats,
            seasonal.clim,
            day,
            base.aoi.name,
            METRICS[options.metric]["unit_short"],
        )
        t = base.axis.index(day)
        lo = max(0, t - 59)
        recent = {
            "dates": [base.axis.date(i).isoformat() for i in range(lo, t + 1)],
            "values": to_list(base.series.value[lo : t + 1], 1),
            "vtype": base.series.vtype[lo : t + 1].astype(int).tolist(),
        }
        week = np.asarray([base.axis.week[i] for i in range(lo, t + 1)])
        recent["climatology_median"] = to_list(seasonal.clim.median[week] / 7.0, 1)
        recent["climatology_p90"] = to_list(seasonal.clim.quantiles[4][week] / 7.0, 1)
        return {
            **self._envelope(base),
            **result,
            "as_of_bounds": {"min": first.isoformat(), "max": last.isoformat(), "default": default.isoformat()},
            "is_latest": day == default,
            "recent_daily": recent,
            "periods": [burncal.period_summary(p, seasonal.stats[p.id]) for p in seasonal.periods],
            "has_map_key": self.settings.firms_map_key is not None,
        }

    # ------------------------------------------------------------------ exports

    def detection_weights(
        self, aoi: Aoi, confidence: str, day_index: np.ndarray, platform: np.ndarray, eligible: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Harmonized weight and value type of single detections.

        The weight says how many VIIRS S-NPP equivalent detections one row stands for, so the weights of a day
        add up to the harmonized value of that day and the sensor of origin stops mattering. A detection of the
        reference sensor weighs 1 on a day that sensor observed. On every other day the harmonized value is
        shared equally among the detections of the sensors it was estimated from. A detection that did not enter
        the estimate (another sensor, a static heat source, a day outside the record) weighs 0.
        """
        base = self.base(aoi, Options(metric="count", confidence=confidence))
        series = base.series
        n_days = base.axis.n_days
        factor = np.zeros((firms.N_PLATFORMS, n_days))
        reference = series.vtype == harmonize.VT_REFERENCE
        factor[harmonize.REFERENCE, reference] = 1.0
        for order, key in enumerate(harmonize.PRIORITY):
            days = np.flatnonzero((series.source_set == order) & ~reference & np.isfinite(series.value))
            if days.size == 0:
                continue
            members = list(harmonize.PREDICTOR_SETS[key])
            total = base.x[members][:, days].sum(axis=0)
            share = np.divide(series.value[days], total, out=np.zeros(days.size), where=total > 0)
            for member in members:
                factor[member, days] = share
        day_index = np.asarray(day_index, dtype=np.int64)
        platform = np.asarray(platform, dtype=np.int64)
        inside = (day_index >= 0) & (day_index < n_days)
        safe = np.where(inside, day_index, 0)
        weight = np.where(inside & np.asarray(eligible, dtype=bool), factor[platform, safe], 0.0)
        vtype = np.where(inside, series.vtype[safe], harmonize.VT_MISSING).astype(np.uint8)
        return weight, vtype

    def export_daily_csv(
        self, aoi: Aoi, options: Options, date_from: dt.date | None = None, date_to: dt.date | None = None
    ) -> str:
        base = self.base(aoi, options)
        axis = base.axis
        first = 0 if date_from is None else max(0, axis.index(date_from))
        last = axis.n_days if date_to is None else min(axis.n_days, axis.index(date_to) + 1)
        if last <= first:
            raise ValueError("The date range lies outside the ingested record")
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(["# AgniSetu harmonized daily burning activity"])
        writer.writerow([f"# Area: {base.aoi.name} ({base.aoi.area_km2:,.0f} square km), id {base.aoi.id}"])
        writer.writerow([f"# Metric: {METRICS[options.metric]['unit']}; confidence filter: {options.confidence}"])
        writer.writerow(
            [
                "# value_type: reference = VIIRS S-NPP observed; viirs_calibrated = NOAA-20 calibrated to "
                "S-NPP; modis_harmonized = MODIS estimate; missing = no sensor coverage"
            ]
        )
        writer.writerow(["# Source: NASA FIRMS (MODIS Collection 6.1, VIIRS Collection 2)"])
        header = ["date", "harmonized", "lower_80", "upper_80", "value_type"]
        header += [f"raw_{p.key.lower()}" for p in firms.PLATFORMS]
        header += [f"observed_{p.key.lower()}" for p in firms.PLATFORMS]
        writer.writerow(header)
        dates = axis.iso_dates()
        s = base.series

        def fmt(v: float) -> str:
            return "" if not np.isfinite(v) else f"{v:.2f}"

        for i in range(first, last):
            row = [dates[i], fmt(s.value[i]), fmt(s.lower[i]), fmt(s.upper[i]), harmonize.VALUE_TYPES[int(s.vtype[i])]]
            row += [f"{base.x[p.id, i]:.2f}" for p in firms.PLATFORMS]
            row += ["1" if base.observed[p.id, i] else "0" for p in firms.PLATFORMS]
            writer.writerow(row)
        return buffer.getvalue()

    def export_calendar_csv(self, aoi: Aoi, options: Options) -> str:
        base = self.base(aoi, options)
        axis = base.axis
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(["# AgniSetu harmonized burning activity calendar (activity per 7 days)"])
        writer.writerow(
            [
                f"# Area: {base.aoi.name}; metric: {METRICS[options.metric]['unit']}; "
                f"confidence filter: {options.confidence}"
            ]
        )
        writer.writerow(
            ["year", "week", "week_start", "week_end", "harmonized", "lower_80", "upper_80", "days_covered"]
        )
        for b in range(axis.n_bins):
            year, week = int(axis.bin_year[b]), int(axis.bin_week[b])
            start, end = bin_dates(year, week)
            v = base.weekly[b]
            writer.writerow(
                [
                    year,
                    week + 1,
                    start.isoformat(),
                    end.isoformat(),
                    "" if not np.isfinite(v) else f"{v:.2f}",
                    "" if not np.isfinite(base.weekly_lower[b]) else f"{base.weekly_lower[b]:.2f}",
                    "" if not np.isfinite(base.weekly_upper[b]) else f"{base.weekly_upper[b]:.2f}",
                    int(base.weekly_days[b]),
                ]
            )
        return buffer.getvalue()

    def report(self, aoi: Aoi, options: Options) -> dict[str, Any]:
        calendar = self.calendar(aoi, options, None, "harmonized", "weekly")
        return {
            "generated_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "product": "AgniSetu harmonized burning activity report",
            "summary": self.summary(aoi, options),
            "calendar": calendar,
            "harmonization": self.harmonization(aoi, options),
            "anomalies": self.anomalies(aoi, options, None, None),
            "warning": self.warning(aoi, options, None),
            "attribution": "Active fire data: NASA FIRMS (MODIS Collection 6.1 and VIIRS Collection 2).",
        }
