"""Sensor harmonization: calibrate MODIS and NOAA-20 to VIIRS S-NPP equivalent activity.

For each predictor set (which platforms stand in for the reference) several candidate models
are fitted on overlap weeks and compared with leave one year out cross validation. The winner
is refitted on all overlap data and used to assemble one continuous daily series.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import firms
from .stats import nan_to_none, poisson_irls
from .timeaxis import N_BINS_PER_YEAR, TimeAxis

REFERENCE = firms.SNPP
PREDICTOR_SETS: dict[str, tuple[int, ...]] = {
    "J1": (firms.NOAA20,),
    "TA": (firms.TERRA, firms.AQUA),
    "A": (firms.AQUA,),
    "T": (firms.TERRA,),
}
SET_LABELS = {
    "J1": "VIIRS NOAA-20",
    "TA": "MODIS Terra + Aqua",
    "A": "MODIS Aqua only",
    "T": "MODIS Terra only",
}
# Order in which predictor sets are tried for a day without a reference observation.
PRIORITY = ("J1", "TA", "A", "T")

VT_MISSING, VT_REFERENCE, VT_VIIRS_CALIBRATED, VT_MODIS_HARMONIZED = 0, 1, 2, 3
VALUE_TYPES = {
    VT_MISSING: "missing",
    VT_REFERENCE: "reference",
    VT_VIIRS_CALIBRATED: "viirs_calibrated",
    VT_MODIS_HARMONIZED: "modis_harmonized",
}

MIN_COVERED_DAYS = 5
MIN_ACTIVE_WEEKS = 26
MIN_PREDICTOR_DETECTIONS = 100
MIN_YEARS = 3
SIMPLICITY_TOLERANCE = 0.02
INTERVAL_PROBS = (0.10, 0.90)

MODEL_LABELS = {
    "identity": "No correction (identity)",
    "ratio": "Single global ratio",
    "seasonal_ratio": "Seasonal ratio",
    "harmonic_glm": "Harmonic GLM",
    "platform_glm": "Platform aware harmonic GLM",
}
MODEL_ORDER = ("identity", "ratio", "seasonal_ratio", "harmonic_glm", "platform_glm")


# --------------------------------------------------------------------------------------------
# Candidate models. xs is [n, k] weekly 7 day rates of the k predictor platforms.
# --------------------------------------------------------------------------------------------


def _harmonics(week: np.ndarray) -> np.ndarray:
    angle = 2.0 * math.pi * (np.asarray(week, dtype=float) + 0.5) / N_BINS_PER_YEAR
    return np.column_stack([np.sin(angle), np.cos(angle), np.sin(2 * angle), np.cos(2 * angle)])


class Identity:
    name = "identity"

    def fit(self, xs: np.ndarray, y: np.ndarray, week: np.ndarray) -> Identity:
        return self

    def predict(self, xs: np.ndarray, week: np.ndarray) -> np.ndarray:
        return xs.sum(axis=1)

    def params(self) -> dict[str, Any]:
        return {}


class GlobalRatio:
    name = "ratio"

    def __init__(self) -> None:
        self.ratio = 1.0

    def fit(self, xs: np.ndarray, y: np.ndarray, week: np.ndarray) -> GlobalRatio:
        total = float(xs.sum())
        self.ratio = float(y.sum()) / total if total > 0 else 1.0
        return self

    def predict(self, xs: np.ndarray, week: np.ndarray) -> np.ndarray:
        return self.ratio * xs.sum(axis=1)

    def params(self) -> dict[str, Any]:
        return {"ratio": round(self.ratio, 4)}


class SeasonalRatio:
    name = "seasonal_ratio"
    half_window = 3

    def __init__(self) -> None:
        self.ratios = np.ones(N_BINS_PER_YEAR)
        self.global_ratio = 1.0

    def fit(self, xs: np.ndarray, y: np.ndarray, week: np.ndarray) -> SeasonalRatio:
        x = xs.sum(axis=1)
        total = float(x.sum())
        self.global_ratio = float(y.sum()) / total if total > 0 else 1.0
        active = x[x > 0]
        prior = float(active.mean()) if active.size else 1.0
        sum_x = np.bincount(week, weights=x, minlength=N_BINS_PER_YEAR)
        sum_y = np.bincount(week, weights=y, minlength=N_BINS_PER_YEAR)
        pooled_x = np.zeros(N_BINS_PER_YEAR)
        pooled_y = np.zeros(N_BINS_PER_YEAR)
        for shift in range(-self.half_window, self.half_window + 1):
            pooled_x += np.roll(sum_x, shift)
            pooled_y += np.roll(sum_y, shift)
        self.ratios = (pooled_y + prior * self.global_ratio) / (pooled_x + prior)
        return self

    def predict(self, xs: np.ndarray, week: np.ndarray) -> np.ndarray:
        return self.ratios[np.asarray(week, dtype=int)] * xs.sum(axis=1)

    def params(self) -> dict[str, Any]:
        return {
            "global_ratio": round(self.global_ratio, 4),
            "min_ratio": round(float(self.ratios.min()), 4),
            "max_ratio": round(float(self.ratios.max()), 4),
        }


class HarmonicGlm:
    """log E[y] = b0 + b1 log(1 + x) + two annual harmonics, quasi Poisson."""

    name = "harmonic_glm"

    def __init__(self) -> None:
        self.beta = np.zeros(6)

    def _design(self, xs: np.ndarray, week: np.ndarray) -> np.ndarray:
        return np.column_stack([np.ones(xs.shape[0]), np.log1p(xs.sum(axis=1)), _harmonics(week)])

    def fit(self, xs: np.ndarray, y: np.ndarray, week: np.ndarray) -> HarmonicGlm:
        self.beta = poisson_irls(self._design(xs, week), y)
        return self

    def predict(self, xs: np.ndarray, week: np.ndarray) -> np.ndarray:
        return np.exp(np.clip(self._design(xs, week) @ self.beta, -30.0, 30.0))

    def params(self) -> dict[str, Any]:
        names = ("intercept", "log1p_x", "sin1", "cos1", "sin2", "cos2")
        return {n: round(float(b), 4) for n, b in zip(names, self.beta, strict=True)}


class PlatformGlm(HarmonicGlm):
    """As HarmonicGlm, with one log term per predictor platform."""

    name = "platform_glm"

    def _design(self, xs: np.ndarray, week: np.ndarray) -> np.ndarray:
        return np.column_stack([np.ones(xs.shape[0]), np.log1p(xs), _harmonics(week)])

    def params(self) -> dict[str, Any]:
        k = self.beta.size - 5
        names = ("intercept", *[f"log1p_x{i + 1}" for i in range(k)], "sin1", "cos1", "sin2", "cos2")
        return {n: round(float(b), 4) for n, b in zip(names, self.beta, strict=True)}


def _candidates(n_predictors: int) -> list[Callable[[], Any]]:
    models: list[Callable[[], Any]] = [Identity, GlobalRatio, SeasonalRatio, HarmonicGlm]
    if n_predictors > 1:
        models.append(PlatformGlm)
    return models


# --------------------------------------------------------------------------------------------
# Skill metrics and intervals
# --------------------------------------------------------------------------------------------


def skill_metrics(y: np.ndarray, yhat: np.ndarray, year: np.ndarray) -> dict[str, float | None]:
    y = np.asarray(y, dtype=float)
    yhat = np.asarray(yhat, dtype=float)
    total = float(y.sum())
    wape = float(np.abs(y - yhat).sum() / total) if total > 0 else float("nan")
    bias = float(yhat.sum() / total - 1.0) if total > 0 else float("nan")
    ly, lh = np.log1p(y), np.log1p(np.clip(yhat, 0, None))
    ss_tot = float(np.sum((ly - ly.mean()) ** 2))
    r2 = 1.0 - float(np.sum((ly - lh) ** 2)) / ss_tot if ss_tot > 0 else float("nan")
    annual_errors = []
    for yr in np.unique(year):
        sel = year == yr
        if int(sel.sum()) >= 40 and y[sel].sum() > 0:
            annual_errors.append(abs(yhat[sel].sum() - y[sel].sum()) / y[sel].sum())
    annual = float(np.mean(annual_errors)) if annual_errors else float("nan")
    return {
        "wape": nan_to_none(wape),
        "bias": nan_to_none(bias),
        "r2_log": nan_to_none(r2),
        "annual_mape": nan_to_none(annual),
    }


@dataclass(frozen=True)
class IntervalModel:
    """Empirical prediction interval from out of fold residuals on the log(1 + y) scale."""

    thresholds: tuple[float, ...]
    low: tuple[float, ...]
    high: tuple[float, ...]

    @staticmethod
    def fit(y: np.ndarray, yhat: np.ndarray) -> IntervalModel:
        resid = np.log1p(y) - np.log1p(np.clip(yhat, 0, None))
        if resid.size >= 90:
            t1, t2 = np.quantile(yhat, [1 / 3, 2 / 3])
            if t1 < t2:
                groups = [yhat <= t1, (yhat > t1) & (yhat <= t2), yhat > t2]
                if all(int(g.sum()) >= 20 for g in groups):
                    lows = tuple(float(np.quantile(resid[g], INTERVAL_PROBS[0])) for g in groups)
                    highs = tuple(float(np.quantile(resid[g], INTERVAL_PROBS[1])) for g in groups)
                    return IntervalModel((float(t1), float(t2)), lows, highs)
        if resid.size >= 8:
            lo, hi = np.quantile(resid, INTERVAL_PROBS)
            return IntervalModel((), (float(lo),), (float(hi),))
        return IntervalModel((), (-math.log(3.0),), (math.log(3.0),))

    def apply(self, yhat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        yhat = np.asarray(yhat, dtype=float)
        group = (
            np.searchsorted(np.asarray(self.thresholds), yhat, side="left")
            if self.thresholds
            else np.zeros(yhat.shape, dtype=int)
        )
        low = np.asarray(self.low)[group]
        high = np.asarray(self.high)[group]
        base = np.log1p(np.clip(yhat, 0, None))
        lower = np.clip(np.expm1(base + np.minimum(low, 0.0)), 0.0, None)
        upper = np.expm1(base + np.maximum(high, 0.0))
        # exp(log(y)) is not exactly y in floating point; keep the bounds on the right side.
        return np.minimum(lower, yhat), np.maximum(upper, yhat)


# --------------------------------------------------------------------------------------------
# Fitting one predictor set
# --------------------------------------------------------------------------------------------


@dataclass
class WeeklyPairs:
    xs: np.ndarray  # [n, k] predictor 7 day rates
    y: np.ndarray  # [n] reference 7 day rate
    week: np.ndarray
    year: np.ndarray
    bin_id: np.ndarray
    x_detections: float  # predictor detections in the overlap, for the sample size rule

    @property
    def n(self) -> int:
        return int(self.y.size)


def weekly_pairs(
    axis: TimeAxis,
    x: np.ndarray,
    x_count: np.ndarray,
    observed: np.ndarray,
    platforms: tuple[int, ...],
    reference: int = REFERENCE,
) -> WeeklyPairs:
    """Matched weekly rates of predictors and reference over days where all of them were observing."""
    joint = observed[reference].copy()
    for p in platforms:
        joint &= observed[p]
    n_bins = axis.n_bins
    days = np.bincount(axis.bin_id[joint], minlength=n_bins)
    valid = days >= MIN_COVERED_DAYS
    scale = np.where(valid, 7.0 / np.maximum(days, 1), 0.0)
    cols = [np.bincount(axis.bin_id[joint], weights=x[p][joint], minlength=n_bins) * scale for p in platforms]
    y = np.bincount(axis.bin_id[joint], weights=x[reference][joint], minlength=n_bins) * scale
    detections = float(sum(x_count[p][joint].sum() for p in platforms))
    xs = np.column_stack(cols)[valid]
    return WeeklyPairs(
        xs,
        y[valid],
        axis.bin_week[valid].astype(int),
        axis.bin_year[valid].astype(int),
        np.flatnonzero(valid),
        detections,
    )


def sufficient(pairs: WeeklyPairs) -> bool:
    if pairs.n == 0:
        return False
    active = int(np.sum(pairs.xs.sum(axis=1) > 0))
    years = np.unique(pairs.year[pairs.xs.sum(axis=1) > 0]).size
    return (
        active >= MIN_ACTIVE_WEEKS
        and years >= MIN_YEARS
        and pairs.x_detections >= MIN_PREDICTOR_DETECTIONS
        and float(pairs.y.sum()) > 0
    )


@dataclass
class SetFit:
    key: str
    platforms: tuple[int, ...]
    scope: str
    scope_label: str
    pairs: WeeklyPairs
    candidates: list[dict[str, Any]]
    selected: str
    model: Any
    interval: IntervalModel
    oof: np.ndarray  # out of fold predictions of the selected model, aligned with pairs
    oof_lower: np.ndarray = field(default_factory=lambda: np.zeros(0))
    oof_upper: np.ndarray = field(default_factory=lambda: np.zeros(0))

    def predict(self, xs: np.ndarray, week: np.ndarray) -> np.ndarray:
        return np.clip(self.model.predict(xs, week), 0.0, None)


def _cross_validate(factory: Callable[[], Any], pairs: WeeklyPairs) -> np.ndarray:
    oof = np.full(pairs.n, np.nan)
    for year in np.unique(pairs.year):
        test = pairs.year == year
        train = ~test
        if not np.any(pairs.xs[train].sum(axis=1) > 0):
            continue
        model = factory().fit(pairs.xs[train], pairs.y[train], pairs.week[train])
        oof[test] = np.clip(model.predict(pairs.xs[test], pairs.week[test]), 0.0, None)
    return oof


def fit_set(key: str, pairs: WeeklyPairs, scope: str, scope_label: str) -> SetFit:
    platforms = PREDICTOR_SETS[key]
    results: list[tuple[str, np.ndarray, dict[str, Any]]] = []
    for factory in _candidates(len(platforms)):
        oof = _cross_validate(factory, pairs)
        ok = np.isfinite(oof)
        metrics = skill_metrics(pairs.y[ok], oof[ok], pairs.year[ok]) if np.any(ok) else {}
        results.append((factory.name, oof, metrics))

    scored = [(name, m.get("wape")) for name, _, m in results if m.get("wape") is not None]
    if not scored:
        raise ValueError("No candidate model could be validated")
    best = min(w for _, w in scored)
    selected = next(
        name
        for name in MODEL_ORDER
        if any(n == name and w <= best * (1.0 + SIMPLICITY_TOLERANCE) + 1e-12 for n, w in scored)
    )
    factory = next(f for f in _candidates(len(platforms)) if f.name == selected)
    model = factory().fit(pairs.xs, pairs.y, pairs.week)
    oof = next(o for n, o, _ in results if n == selected)
    ok = np.isfinite(oof)
    interval = IntervalModel.fit(pairs.y[ok], oof[ok])
    lower, upper = interval.apply(np.where(ok, oof, 0.0))
    candidates = []
    for name, _, metrics in results:
        fitted = (
            model
            if name == selected
            else next(f for f in _candidates(len(platforms)) if f.name == name)().fit(pairs.xs, pairs.y, pairs.week)
        )
        candidates.append(
            {
                "name": name,
                "label": MODEL_LABELS[name],
                "selected": name == selected,
                "params": fitted.params(),
                **{k: metrics.get(k) for k in ("wape", "bias", "r2_log", "annual_mape")},
            }
        )
    return SetFit(key, platforms, scope, scope_label, pairs, candidates, selected, model, interval, oof, lower, upper)


ScopeProvider = Callable[[str], tuple[np.ndarray, np.ndarray] | None]
SCOPES: tuple[tuple[str, str], ...] = (
    ("area", "the selected area"),
    ("buffer_1deg", "the area's bounding box widened by 1 degree"),
    ("buffer_3deg", "the area's bounding box widened by 3 degrees"),
    ("domain", "the whole ingested domain"),
)


def fit_all_sets(
    axis: TimeAxis, observed: np.ndarray, scope_provider: ScopeProvider
) -> tuple[dict[str, SetFit], dict[str, str]]:
    """Fit every predictor set, widening the training region until the sample size rule is met."""
    fits: dict[str, SetFit] = {}
    unavailable: dict[str, str] = {}
    cache: dict[str, tuple[np.ndarray, np.ndarray] | None] = {}
    for key, platforms in PREDICTOR_SETS.items():
        joint = observed[REFERENCE].copy()
        for p in platforms:
            joint &= observed[p]
        if int(joint.sum()) < 7 * MIN_ACTIVE_WEEKS:
            unavailable[key] = "The reference sensor and this predictor never observed together long enough."
            continue
        for scope, label in SCOPES:
            if scope not in cache:
                cache[scope] = scope_provider(scope)
            data = cache[scope]
            if data is None:
                continue
            x, x_count = data
            pairs = weekly_pairs(axis, x, x_count, observed, platforms)
            if sufficient(pairs):
                fits[key] = fit_set(key, pairs, scope, label)
                break
        else:
            unavailable[key] = "Too few overlapping detections, even across the whole domain."
    return fits, unavailable


# --------------------------------------------------------------------------------------------
# Assembling the continuous daily series
# --------------------------------------------------------------------------------------------


@dataclass
class HarmonizedSeries:
    value: np.ndarray  # float64 [day], NaN where missing
    lower: np.ndarray
    upper: np.ndarray
    vtype: np.ndarray  # uint8 [day]
    source_set: np.ndarray  # int8 [day], index into PRIORITY or -1

    def counts_by_type(self) -> dict[str, int]:
        return {VALUE_TYPES[t]: int(np.sum(self.vtype == t)) for t in VALUE_TYPES}


def assemble(axis: TimeAxis, x: np.ndarray, observed: np.ndarray, fits: dict[str, SetFit]) -> HarmonizedSeries:
    n = axis.n_days
    value = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    upper = np.full(n, np.nan)
    vtype = np.zeros(n, dtype=np.uint8)
    source = np.full(n, -1, dtype=np.int8)

    ref = observed[REFERENCE]
    value[ref] = x[REFERENCE][ref]
    lower[ref] = value[ref]
    upper[ref] = value[ref]
    vtype[ref] = VT_REFERENCE
    remaining = ~ref

    for order, key in enumerate(PRIORITY):
        fit = fits.get(key)
        if fit is None:
            continue
        usable = remaining.copy()
        for p in fit.platforms:
            usable &= observed[p]
        if not np.any(usable):
            continue
        bins = axis.bin_id[usable]
        n_bins = axis.n_bins
        days = np.bincount(bins, minlength=n_bins).astype(float)
        sums = np.column_stack([np.bincount(bins, weights=x[p][usable], minlength=n_bins) for p in fit.platforms])
        active = days > 0
        rates = sums[active] * (7.0 / days[active])[:, None]
        yhat7 = np.zeros(n_bins)
        yhat7[active] = fit.predict(rates, axis.bin_week[active].astype(int))
        lo7 = np.zeros(n_bins)
        hi7 = np.zeros(n_bins)
        lo7[active], hi7[active] = fit.interval.apply(yhat7[active])

        total = sums.sum(axis=1)
        day_total = np.zeros(int(usable.sum()))
        for p in fit.platforms:
            day_total += x[p][usable]
        with np.errstate(divide="ignore", invalid="ignore"):
            share = np.where(total[bins] > 0, day_total / total[bins], 1.0 / days[bins])
        factor = days[bins] / 7.0 * share
        value[usable] = yhat7[bins] * factor
        lower[usable] = lo7[bins] * factor
        upper[usable] = hi7[bins] * factor
        vtype[usable] = VT_VIIRS_CALIBRATED if key == "J1" else VT_MODIS_HARMONIZED
        source[usable] = order
        remaining &= ~usable

    return HarmonizedSeries(value, lower, upper, vtype, source)


def effective_factor_curve(fit: SetFit) -> dict[str, Any]:
    """VIIRS equivalent per predictor detection through the year, at a typical active week."""
    totals = fit.pairs.xs.sum(axis=1)
    active = totals[totals > 0]
    if active.size == 0:
        return {"typical_x": None, "factor": [None] * N_BINS_PER_YEAR}
    typical = float(np.median(active))
    mix = fit.pairs.xs[totals > 0].sum(axis=0)
    mix = mix / mix.sum() if mix.sum() > 0 else np.full(mix.size, 1.0 / mix.size)
    weeks = np.arange(N_BINS_PER_YEAR)
    xs = np.tile(mix * typical, (N_BINS_PER_YEAR, 1))
    factor = fit.predict(xs, weeks) / typical
    return {"typical_x": round(typical, 2), "factor": [round(float(v), 4) for v in factor]}
