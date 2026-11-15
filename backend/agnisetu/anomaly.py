"""Unusual conditions: season, weekly and timing anomalies, and long term trends."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .burncal import CriticalPeriod, SeasonStats, offset_to_label
from .stats import mann_kendall, midrank_percentile, nan_to_none, robust_z, theil_sen
from .timeaxis import N_BINS_PER_YEAR

CLASSES = ("far_below", "below", "near_normal", "above", "far_above")
CLASS_LABELS = {
    "far_below": "Far below normal",
    "below": "Below normal",
    "near_normal": "Near normal",
    "above": "Above normal",
    "far_above": "Far above normal",
}
MIN_REFERENCE_YEARS = 5


def classify_percentile(percentile: float) -> str | None:
    if not math.isfinite(percentile):
        return None
    if percentile <= 10:
        return "far_below"
    if percentile <= 25:
        return "below"
    if percentile < 75:
        return "near_normal"
    if percentile < 90:
        return "above"
    return "far_above"


def season_anomalies(stats: SeasonStats, baseline_mask: np.ndarray) -> list[dict[str, Any]]:
    """Score every year's season total against all other baseline years (leave one out)."""
    rows: list[dict[str, Any]] = []
    totals = stats.total
    for i, year in enumerate(stats.years):
        value = totals[i]
        if not np.isfinite(value):
            rows.append({"year": year, "total": None, "available": False})
            continue
        ref_mask = baseline_mask & np.isfinite(totals)
        ref_mask[i] = False
        reference = totals[ref_mask]
        if reference.size < MIN_REFERENCE_YEARS:
            rows.append({"year": year, "total": nan_to_none(value, 1), "available": False})
            continue
        median = float(np.median(reference))
        mean = float(np.mean(reference))
        percentile = midrank_percentile(float(value), reference)
        z = robust_z(float(value), reference)
        rows.append(
            {
                "year": year,
                "available": True,
                "total": nan_to_none(value, 1),
                "lower": nan_to_none(stats.lower[i], 1),
                "upper": nan_to_none(stats.upper[i], 1),
                "estimated_share": nan_to_none(stats.estimated_share[i], 3),
                "reference_median": nan_to_none(median, 1),
                "ratio_to_median": nan_to_none(value / median if median > 0 else None, 3),
                "reference_mean": nan_to_none(mean, 1),
                "anomaly_vs_mean": nan_to_none((value - mean) / mean if mean > 0 else None, 3),
                "percentile": nan_to_none(percentile, 1),
                "robust_z": nan_to_none(z, 2),
                "class": classify_percentile(percentile),
                "record_high": bool(value > reference.max()),
                "record_low": bool(value < reference.min()),
                "onset": offset_to_label(stats.onset[i]),
                "peak": offset_to_label(stats.peak[i]),
                "end": offset_to_label(stats.end[i]),
                "onset_offset": nan_to_none(stats.onset[i], 0),
                "peak_offset": nan_to_none(stats.peak[i], 0),
                "end_offset": nan_to_none(stats.end[i], 0),
                "onset_shift_days": _shift(stats.onset, i),
                "peak_shift_days": _shift(stats.peak, i),
                "end_shift_days": _shift(stats.end, i),
            }
        )
    return rows


def _shift(values: np.ndarray, index: int) -> float | None:
    if not np.isfinite(values[index]):
        return None
    others = np.delete(values, index)
    others = others[np.isfinite(others)]
    if others.size < MIN_REFERENCE_YEARS:
        return None
    return nan_to_none(float(values[index] - np.median(others)), 0)


def weekly_anomaly_matrix(matrix: np.ndarray, baseline_mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Mid rank percentile of each cell against the same week in other baseline years.

    Returns (percentile matrix, class index matrix with -1 for unavailable).
    """
    n_years = matrix.shape[0]
    percentile = np.full(matrix.shape, np.nan)
    classes = np.full(matrix.shape, -1, dtype=np.int8)
    for week in range(N_BINS_PER_YEAR):
        column = matrix[:, week]
        for i in range(n_years):
            value = column[i]
            if not np.isfinite(value):
                continue
            ref_mask = baseline_mask & np.isfinite(column)
            ref_mask[i] = False
            reference = column[ref_mask]
            if reference.size < MIN_REFERENCE_YEARS:
                continue
            p = midrank_percentile(float(value), reference)
            percentile[i, week] = p
            label = classify_percentile(p)
            if label is not None:
                classes[i, week] = CLASSES.index(label)
    return percentile, classes


def _trend(years: np.ndarray, values: np.ndarray, relative: bool) -> dict[str, Any]:
    ok = np.isfinite(values)
    if int(ok.sum()) < 8:
        return {"available": False, "n": int(ok.sum())}
    slope, intercept = theil_sen(years[ok].astype(float), values[ok])
    _, z, p = mann_kendall(values[ok])
    median = float(np.median(values[ok]))
    out: dict[str, Any] = {
        "available": True,
        "n": int(ok.sum()),
        "first_year": int(years[ok][0]),
        "last_year": int(years[ok][-1]),
        "slope_per_year": nan_to_none(slope, 4),
        "intercept": nan_to_none(intercept, 4),
        "slope_per_decade": nan_to_none(slope * 10.0, 3),
        "mk_z": nan_to_none(z, 3),
        "p_value": nan_to_none(p, 4),
        "significant": bool(p < 0.05),
        "direction": "increasing" if slope > 0 else ("decreasing" if slope < 0 else "flat"),
    }
    if relative:
        out["percent_per_decade"] = nan_to_none(slope * 10.0 / median * 100.0 if median > 0 else None, 1)
    return out


def _p_text(p_value: float | None) -> str:
    """Report a p value the way a reader expects: never as an impossible exact zero."""
    if p_value is None:
        return "p not available"
    return "p below 0.001" if p_value < 0.001 else f"p = {p_value:.3f}"


def describe_trend(name: str, trend: dict[str, Any], unit: str) -> str:
    if not trend.get("available"):
        return f"Not enough years to test a trend in {name}."
    span = f"{trend['first_year']} to {trend['last_year']}"
    strength = "statistically significant" if trend["significant"] else "not statistically significant"
    if unit == "percent":
        change = trend.get("percent_per_decade")
        if change is None:
            return f"No measurable trend in {name} over {span}."
        verb = "rose" if change > 0 else "fell"
        return (
            f"{name.capitalize()} {verb} by about {abs(change):.0f} percent per decade over {span} "
            f"({strength}, Mann-Kendall {_p_text(trend['p_value'])})."
        )
    change = trend["slope_per_decade"]
    if change is None or abs(change) < 0.5:
        return f"{name.capitalize()} shows no meaningful shift over {span} ({strength}, {_p_text(trend['p_value'])})."
    verb = "later" if change > 0 else "earlier"
    return (
        f"{name.capitalize()} moved about {abs(change):.0f} days {verb} per decade over {span} "
        f"({strength}, Mann-Kendall {_p_text(trend['p_value'])})."
    )


def season_trends(period: CriticalPeriod, stats: SeasonStats, baseline_mask: np.ndarray) -> dict[str, Any]:
    years = np.asarray(stats.years)
    totals = np.where(baseline_mask, stats.total, np.nan)
    onset = np.where(baseline_mask, stats.onset, np.nan)
    peak = np.where(baseline_mask, stats.peak, np.nan)
    total_trend = _trend(years, totals, relative=True)
    onset_trend = _trend(years, onset, relative=False)
    peak_trend = _trend(years, peak, relative=False)
    return {
        "period_id": period.id,
        "total": {**total_trend, "text": describe_trend("season activity", total_trend, "percent")},
        "onset": {**onset_trend, "text": describe_trend("season onset", onset_trend, "days")},
        "peak": {**peak_trend, "text": describe_trend("the season peak", peak_trend, "days")},
    }
