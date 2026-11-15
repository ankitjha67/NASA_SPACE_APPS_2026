"""Early warning: alert level, season progress, next critical period and a two week outlook.

Everything here is a regional statistical statement about burning activity. Nothing in this
module predicts where or when an individual fire will start.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

import numpy as np

from .burncal import (
    MIN_WINDOW_COVERAGE,
    MONTHS,
    Climatology,
    CriticalPeriod,
    SeasonStats,
    monthly_totals,
    offset_to_label,
)
from .harmonize import VALUE_TYPES, VT_REFERENCE
from .stats import midrank_percentile, nan_to_none, quantiles
from .timeaxis import TimeAxis

LEVELS = ("minimal", "below_normal", "normal", "elevated", "high", "extreme")
LEVEL_LABELS = {
    "minimal": "Minimal",
    "below_normal": "Below normal",
    "normal": "Normal",
    "elevated": "Elevated",
    "high": "High",
    "extreme": "Extreme",
}
MINIMAL_ABSOLUTE = 5.0
MINIMAL_PEAK_FRACTION = 0.02
ANCHOR_OFFSETS = tuple(range(-28, 29, 7))
MIN_HINDCAST_PAIRS = 30
MIN_REFERENCE_YEARS = 5
MIN_USEFUL_SKILL = 0.05
ENVELOPE_PROBS = (0.10, 0.25, 0.50, 0.75, 0.90)


def same_day(year: int, month: int, day: int) -> dt.date:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return dt.date(year, month, 28)


class WindowSums:
    """Window totals over a daily series, scaled for missing days and NaN when coverage is poor."""

    def __init__(self, value: np.ndarray) -> None:
        finite = np.isfinite(value)
        self.n = int(value.size)
        self._sum = np.concatenate(([0.0], np.cumsum(np.where(finite, value, 0.0))))
        self._have = np.concatenate(([0], np.cumsum(finite.astype(np.int64))))

    def totals(self, end_index: np.ndarray, length: int) -> np.ndarray:
        end_index = np.asarray(end_index, dtype=np.int64)
        lo = end_index - length + 1
        hi = end_index + 1
        ok = (lo >= 0) & (hi <= self.n)
        lo_c = np.clip(lo, 0, self.n)
        hi_c = np.clip(hi, 0, self.n)
        have = self._have[hi_c] - self._have[lo_c]
        total = self._sum[hi_c] - self._sum[lo_c]
        with np.errstate(divide="ignore", invalid="ignore"):
            scaled = np.where(ok & (have >= MIN_WINDOW_COVERAGE * length), total * length / np.maximum(have, 1), np.nan)
        return scaled

    def total(self, end_index: int, length: int) -> float:
        return float(self.totals(np.array([end_index]), length)[0])


def _alert_level(value: float, percentile: float, reference: np.ndarray, peak_median: float) -> tuple[str, str]:
    floor = (
        max(MINIMAL_ABSOLUTE, MINIMAL_PEAK_FRACTION * peak_median) if math.isfinite(peak_median) else MINIMAL_ABSOLUTE
    )
    if value < floor:
        return "minimal", (
            f"The 7 day total is below the activity floor of {floor:,.0f} "
            "(the larger of 5 and 2 percent of the peak week median)."
        )
    if reference.size and value > float(np.max(reference)):
        return "extreme", "The 7 day total is higher than in every other year for this time of year."
    if percentile > 95:
        return "extreme", "The 7 day total is above the 95th percentile for this time of year."
    if percentile >= 90:
        return "high", "The 7 day total is between the 90th and 95th percentile for this time of year."
    if percentile >= 75:
        return "elevated", "The 7 day total is between the 75th and 90th percentile for this time of year."
    if percentile >= 25:
        return "normal", "The 7 day total is between the 25th and 75th percentile for this time of year."
    return "below_normal", "The 7 day total is below the 25th percentile for this time of year."


def _recent_window(
    sums: WindowSums, axis: TimeAxis, as_of: dt.date, other_years: list[int], length: int
) -> dict[str, Any]:
    t = axis.index(as_of)
    value = sums.total(t, length)
    anchors = np.array([axis.index(same_day(y, as_of.month, as_of.day)) for y in other_years])
    reference_all = sums.totals(anchors, length)
    ok = np.isfinite(reference_all)
    reference = reference_all[ok]
    out: dict[str, Any] = {
        "days": length,
        "start": (as_of - dt.timedelta(days=length - 1)).isoformat(),
        "end": as_of.isoformat(),
        "value": nan_to_none(value, 1),
        "reference_years": int(reference.size),
        "available": bool(math.isfinite(value) and reference.size >= MIN_REFERENCE_YEARS),
    }
    if reference.size:
        q = quantiles(reference, (0.10, 0.25, 0.50, 0.75, 0.90))
        out.update(
            {
                "reference_p10": nan_to_none(q[0], 1),
                "reference_p25": nan_to_none(q[1], 1),
                "reference_median": nan_to_none(q[2], 1),
                "reference_p75": nan_to_none(q[3], 1),
                "reference_p90": nan_to_none(q[4], 1),
                "reference_max": nan_to_none(float(reference.max()), 1),
                "history": [
                    {"year": int(y), "value": nan_to_none(v, 1)}
                    for y, v in zip(np.asarray(other_years)[ok], reference, strict=True)
                ],
            }
        )
    if out["available"]:
        median = float(np.median(reference))
        out["percentile"] = nan_to_none(midrank_percentile(value, reference), 1)
        out["ratio_to_median"] = nan_to_none(value / median if median > 0 else None, 2)
    elif not math.isfinite(value):
        out["reason"] = "Fewer than 80 percent of the days in this window have been ingested."
    else:
        out["reason"] = "Too few other years have data for this calendar window."
    return out


def _month_to_date(
    sums: WindowSums, axis: TimeAxis, value: np.ndarray, as_of: dt.date, other_years: list[int]
) -> dict[str, Any]:
    """The current calendar month so far against the same days of that month in other years."""
    elapsed = as_of.day
    window = _recent_window(sums, axis, as_of, other_years, elapsed)
    totals, _, lengths = monthly_totals(axis, value)
    column = totals[:, as_of.month - 1]
    years = np.asarray(axis.years)
    others = np.isfinite(column) & (years != as_of.year)
    out = {
        **window,
        "month_name": MONTHS[as_of.month - 1],
        "label": f"{MONTHS[as_of.month - 1]} {as_of.year}",
        "days_elapsed": elapsed,
        "days_in_month": int(lengths[as_of.year - axis.first_year, as_of.month - 1]),
        "full_month_median": nan_to_none(float(np.median(column[others])) if others.any() else None, 1),
        "full_month_years": int(others.sum()),
        "highest": None,
    }
    if others.any():
        best = int(np.argmax(np.where(others, column, -np.inf)))
        out["highest"] = {"year": int(years[best]), "value": nan_to_none(float(column[best]), 1)}
    return out


def _locate_period(periods: list[CriticalPeriod], as_of: dt.date) -> tuple[CriticalPeriod, int, bool] | None:
    """Find the period whose window contains the date. Returns (period, season year, in core)."""
    extended_hit: tuple[CriticalPeriod, int, bool] | None = None
    for period in periods:
        for season_year in (as_of.year, as_of.year - 1):
            core_start, core_end = period.core_window(season_year)
            if core_start <= as_of <= core_end:
                return period, season_year, True
            ext_start, ext_end = period.extended_window(season_year)
            if ext_start <= as_of <= ext_end and extended_hit is None:
                extended_hit = (period, season_year, False)
    return extended_hit


def _season_progress(
    axis: TimeAxis, value: np.ndarray, period: CriticalPeriod, season_year: int, as_of: dt.date
) -> dict[str, Any]:
    ext_start, ext_end = period.extended_window(season_year)
    length = (ext_end - ext_start).days + 1
    curves = []
    curve_years = []
    for year in axis.years:
        if year == season_year:
            continue
        start, _ = period.extended_window(year)
        lo = axis.index(start)
        hi = lo + length
        if lo < 0 or hi > axis.n_days:
            continue
        seg = value[lo:hi]
        finite = np.isfinite(seg)
        if finite.sum() < MIN_WINDOW_COVERAGE * length:
            continue
        curves.append(np.cumsum(np.where(finite, seg, 0.0)))
        curve_years.append(year)
    out: dict[str, Any] = {
        "period_id": period.id,
        "period_name": period.name,
        "season_year": season_year,
        "window_start": ext_start.isoformat(),
        "window_end": ext_end.isoformat(),
        "reference_years": curve_years,
        "available": False,
    }
    if len(curves) < MIN_REFERENCE_YEARS:
        out["reason"] = "Too few other years have complete data for this period."
        return out
    stack = np.vstack(curves)
    envelope = np.quantile(stack, ENVELOPE_PROBS, axis=0)
    step = max(1, length // 120)
    idx = np.arange(0, length, step)
    if idx[-1] != length - 1:
        idx = np.append(idx, length - 1)
    out["envelope"] = {
        "offsets": idx.tolist(),
        "dates": [(ext_start + dt.timedelta(days=int(i))).isoformat() for i in idx],
        **{f"p{round(p * 100)}": np.round(envelope[k][idx], 1).tolist() for k, p in enumerate(ENVELOPE_PROBS)},
    }
    lo = axis.index(ext_start)
    t = axis.index(as_of)
    elapsed = t - lo + 1
    if lo < 0 or elapsed < 1:
        out["reason"] = "The period window starts before the record."
        return out
    seg = value[lo : t + 1]
    finite = np.isfinite(seg)
    if finite.sum() < MIN_WINDOW_COVERAGE * elapsed:
        missing = int(elapsed - finite.sum())
        out["reason"] = (
            f"{missing} of the {elapsed} days since the period window opened have not been ingested, "
            "so the season to date total is withheld. Backfill with a FIRMS MAP_KEY to enable it."
        )
        return out
    current = np.cumsum(np.where(finite, seg, 0.0)) * (elapsed / finite.sum())
    at = float(current[-1])
    same_point = stack[:, elapsed - 1]
    cur_idx = np.arange(0, elapsed, step)
    if cur_idx[-1] != elapsed - 1:
        cur_idx = np.append(cur_idx, elapsed - 1)
    median_final = float(np.median(stack[:, -1]))
    out.update(
        {
            "available": True,
            "current": {
                "offsets": cur_idx.tolist(),
                "dates": [(ext_start + dt.timedelta(days=int(i))).isoformat() for i in cur_idx],
                "values": np.round(current[cur_idx], 1).tolist(),
            },
            "to_date": nan_to_none(at, 1),
            "to_date_median": nan_to_none(float(np.median(same_point)), 1),
            "to_date_percentile": nan_to_none(midrank_percentile(at, same_point), 1),
            "share_of_typical_season": nan_to_none(at / median_final if median_final > 0 else None, 3),
            "days_elapsed": int(elapsed),
            "days_total": int(length),
        }
    )
    return out


def _next_period(periods: list[CriticalPeriod], stats: dict[str, SeasonStats], as_of: dt.date) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for period in periods:
        season = stats[period.id]
        onset = season.onset[np.isfinite(season.onset)]
        end = season.end[np.isfinite(season.end)]
        if onset.size < 3:
            continue
        onset_median = float(np.median(onset))
        end_median = float(np.median(end)) if end.size else onset_median
        for year in (as_of.year - 1, as_of.year, as_of.year + 1):
            start_date = dt.date(year, 1, 1) + dt.timedelta(days=round(onset_median))
            end_date = dt.date(year, 1, 1) + dt.timedelta(days=round(end_median))
            if start_date <= as_of <= end_date:
                return {
                    "status": "in_progress",
                    "period_id": period.id,
                    "period_name": period.name,
                    "typical_onset": start_date.isoformat(),
                    "typical_end": end_date.isoformat(),
                    "day_of_period": (as_of - start_date).days + 1,
                    "typical_length_days": (end_date - start_date).days + 1,
                    "days_to_typical_end": (end_date - as_of).days,
                }
            if start_date > as_of:
                delta = (start_date - as_of).days
                if best is None or delta < best["days_until"]:
                    best = {
                        "status": "upcoming",
                        "period_id": period.id,
                        "period_name": period.name,
                        "typical_onset": start_date.isoformat(),
                        "typical_end": end_date.isoformat(),
                        "days_until": delta,
                        "earliest_onset": offset_to_label(float(onset.min())),
                        "latest_onset": offset_to_label(float(onset.max())),
                    }
    return best


def _outlook(
    sums: WindowSums, axis: TimeAxis, as_of: dt.date, other_years: list[int], smoothing: float
) -> list[dict[str, Any]]:
    t = axis.index(as_of)
    k = smoothing
    anchors = np.array(
        [[axis.index(same_day(y, as_of.month, as_of.day)) + off for off in ANCHOR_OFFSETS] for y in other_years]
    )
    recent = sums.totals(anchors, 14)
    zero = ANCHOR_OFFSETS.index(0)
    results = []
    current_recent = sums.total(t, 14)
    for horizon in (1, 2):
        target = sums.totals(anchors + 7 * horizon, 7)
        start = as_of + dt.timedelta(days=7 * (horizon - 1) + 1)
        end = as_of + dt.timedelta(days=7 * horizon)
        clim = target[:, zero]
        clim = clim[np.isfinite(clim)]
        entry: dict[str, Any] = {
            "horizon_weeks": horizon,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "climatology_median": nan_to_none(float(np.median(clim)) if clim.size else None, 1),
            "climatology_p10": nan_to_none(quantiles(clim, (0.10,))[0], 1),
            "climatology_p90": nan_to_none(quantiles(clim, (0.90,))[0], 1),
        }
        if clim.size < MIN_REFERENCE_YEARS:
            entry.update({"available": False, "reason": "Too few other years have data for this window."})
            results.append(entry)
            continue

        # Leave one year out medians, so a year never helps define its own normal.
        n_years, n_off = recent.shape
        a = np.full((n_years, n_off), np.nan)
        b = np.full((n_years, n_off), np.nan)
        base = np.full((n_years, n_off), np.nan)
        for i in range(n_years):
            others = np.arange(n_years) != i
            for j in range(n_off):
                r_ref = recent[others, j]
                t_ref = target[others, j]
                r_ref = r_ref[np.isfinite(r_ref)]
                t_ref = t_ref[np.isfinite(t_ref)]
                if r_ref.size < MIN_REFERENCE_YEARS or t_ref.size < MIN_REFERENCE_YEARS:
                    continue
                if not (np.isfinite(recent[i, j]) and np.isfinite(target[i, j])):
                    continue
                r_med = float(np.median(r_ref))
                t_med = float(np.median(t_ref))
                a[i, j] = math.log((recent[i, j] + k) / (r_med + k))
                b[i, j] = math.log((target[i, j] + k) / (t_med + k))
                base[i, j] = t_med
        ok = np.isfinite(a) & np.isfinite(b)
        pairs = int(ok.sum())
        method = "climatology"
        skill = None
        rho = None
        q_lo = q_hi = None
        if pairs >= MIN_HINDCAST_PAIRS:
            predicted = np.full(a.shape, np.nan)
            for i in range(n_years):
                train = ok.copy()
                train[i, :] = False
                denom = float(np.sum(a[train] ** 2))
                rho_i = float(np.sum(a[train] * b[train])) / denom if denom > 0 else 0.0
                rho_i = min(max(rho_i, 0.0), 1.2)
                predicted[i, :] = rho_i * a[i, :]
            actual = (base + k) * np.exp(b) - k
            model_value = (base + k) * np.exp(predicted) - k
            err_model = float(np.sum(np.abs(actual[ok] - model_value[ok])))
            err_clim = float(np.sum(np.abs(actual[ok] - base[ok])))
            skill = 1.0 - err_model / err_clim if err_clim > 0 else None
            denom = float(np.sum(a[ok] ** 2))
            rho = min(max(float(np.sum(a[ok] * b[ok])) / denom if denom > 0 else 0.0, 0.0), 1.2)
            resid = (b - predicted)[ok]
            q_lo, q_hi = (float(v) for v in np.quantile(resid, (0.10, 0.90)))
            if skill is not None and skill >= MIN_USEFUL_SKILL and math.isfinite(current_recent):
                method = "persistence_scaled_climatology"

        median = float(np.median(clim))
        if method == "persistence_scaled_climatology":
            ref_recent = recent[:, zero]
            ref_recent = ref_recent[np.isfinite(ref_recent)]
            anomaly = math.log((current_recent + k) / (float(np.median(ref_recent)) + k))
            center = (median + k) * math.exp(rho * anomaly) - k
            lower = (median + k) * math.exp(rho * anomaly + min(q_lo, 0.0)) - k
            upper = (median + k) * math.exp(rho * anomaly + max(q_hi, 0.0)) - k
            entry.update(
                {
                    "available": True,
                    "method": method,
                    "method_label": "Persistence scaled climatology",
                    "expected": nan_to_none(max(center, 0.0), 1),
                    "lower": nan_to_none(max(lower, 0.0), 1),
                    "upper": nan_to_none(max(upper, 0.0), 1),
                    "recent_anomaly_ratio": nan_to_none(math.exp(anomaly), 2),
                    "persistence": nan_to_none(rho, 3),
                    "skill_vs_climatology": nan_to_none(skill, 3),
                    "hindcast_pairs": pairs,
                }
            )
        else:
            q10, q90 = quantiles(clim, (0.10, 0.90))
            reason = (
                "Recent days are not ingested, so the outlook cannot use current conditions."
                if not math.isfinite(current_recent)
                else "The persistence model showed no useful skill over climatology in hindcasts for this season."
                if pairs >= MIN_HINDCAST_PAIRS
                else "Too few hindcast cases to fit the persistence model."
            )
            entry.update(
                {
                    "available": True,
                    "method": "climatology",
                    "method_label": "Climatology",
                    "expected": nan_to_none(median, 1),
                    "lower": nan_to_none(q10, 1),
                    "upper": nan_to_none(q90, 1),
                    "skill_vs_climatology": nan_to_none(skill, 3),
                    "hindcast_pairs": pairs,
                    "fallback_reason": reason,
                }
            )
        results.append(entry)
    return results


def _short_date(text: str) -> str:
    day = dt.date.fromisoformat(text)
    return f"{day:%b} {day.day}"


def _long_date(text: str) -> str:
    day = dt.date.fromisoformat(text)
    return f"{day:%B} {day.day}"


def _format_amount(value: float | None, unit_short: str) -> str:
    if value is None:
        return "not available"
    if value >= 100:
        return f"{math.floor(value + 0.5):,} {unit_short}"
    return f"{value:,.1f} {unit_short}"


def _bulletin(
    area_name: str,
    as_of: dt.date,
    unit_short: str,
    level: str | None,
    rule: str | None,
    week: dict[str, Any],
    sources: dict[str, int],
    progress: dict[str, Any] | None,
    upcoming: dict[str, Any] | None,
    outlook: list[dict[str, Any]],
) -> list[str]:
    day = f"{as_of:%B} {as_of.day}, {as_of.year}"
    lines: list[str] = []
    if level is None:
        lines.append(
            f"{area_name}: burning activity status for {day} is withheld because recent days are not ingested."
        )
    else:
        lines.append(f"{area_name}: {LEVEL_LABELS[level].lower()} burning activity as of {day}.")
    if week.get("available"):
        ratio = week.get("ratio_to_median")
        ratio_text = f", {ratio:.1f} times the median for this time of year" if ratio is not None else ""
        lines.append(
            f"The 7 days to {as_of:%B} {as_of.day} recorded {_format_amount(week['value'], unit_short)}{ratio_text} "
            f"(percentile {week['percentile']:.0f} of {week['reference_years']} other years). {rule}"
        )
    estimated = sum(v for key, v in sources.items() if key not in ("reference", "missing"))
    if estimated:
        lines.append(
            f"{estimated} of the last 7 days are calibrated estimates from other sensors because the reference "
            "sensor (VIIRS S-NPP) did not report."
        )
    if progress is not None and progress.get("available"):
        lines.append(
            f"Season to date ({progress['period_name']}): {_format_amount(progress['to_date'], unit_short)}, "
            f"percentile {progress['to_date_percentile']:.0f} against the same point in other years."
        )
    elif progress is not None:
        lines.append(f"Season to date ({progress['period_name']}): {progress.get('reason', 'not available')}")
    if upcoming is not None:
        if upcoming["status"] == "in_progress":
            lines.append(
                f"The {upcoming['period_name']} is typically under way now: day {upcoming['day_of_period']} of about "
                f"{upcoming['typical_length_days']}, with about {max(upcoming['days_to_typical_end'], 0)} days "
                "to its usual end."
            )
        else:
            lines.append(
                f"Next critical period: {upcoming['period_name']}, typically starting around "
                f"{_long_date(upcoming['typical_onset'])} (in {upcoming['days_until']} days). "
                f"Earliest recorded onset: {upcoming['earliest_onset']}."
            )
    for item in outlook:
        if not item.get("available"):
            continue
        skill = item.get("skill_vs_climatology")
        basis = (
            f"persistence scaled climatology, hindcast skill {skill * 100:.0f} percent better than climatology"
            if item["method"] != "climatology" and skill is not None
            else "climatology of other years"
        )
        lines.append(
            f"Outlook for {_short_date(item['start'])} to {_short_date(item['end'])}: "
            f"about {_format_amount(item['expected'], unit_short)} "
            f"(80 percent interval {_format_amount(item['lower'], unit_short)} to "
            f"{_format_amount(item['upper'], unit_short)}; {basis})."
        )
    lines.append(
        "This is a regional statistical statement about burning activity from satellite active fire detections. "
        "It does not predict where or when individual fires will start."
    )
    return lines


def build_warning(
    axis: TimeAxis,
    value: np.ndarray,
    vtype: np.ndarray,
    periods: list[CriticalPeriod],
    stats: dict[str, SeasonStats],
    clim: Climatology,
    as_of: dt.date,
    area_name: str,
    unit_short: str,
) -> dict[str, Any]:
    t = axis.index(as_of)
    if not 14 <= t < axis.n_days:
        raise ValueError("The as of date is outside the ingested record")
    sums = WindowSums(value)
    other_years = [y for y in axis.years if y != as_of.year]
    week = _recent_window(sums, axis, as_of, other_years, 7)
    fortnight = _recent_window(sums, axis, as_of, other_years, 14)

    peak_median = float(np.nanmax(clim.median)) if np.any(np.isfinite(clim.median)) else float("nan")
    level = rule = None
    if week["available"]:
        reference = np.array([h["value"] for h in week["history"]], dtype=float)
        level, rule = _alert_level(week["value"], week["percentile"], reference, peak_median)

    recent_types = vtype[max(0, t - 6) : t + 1]
    sources = {VALUE_TYPES[int(k)]: int(np.sum(recent_types == k)) for k in VALUE_TYPES}

    located = _locate_period(periods, as_of)
    progress = _season_progress(axis, value, located[0], located[1], as_of) if located else None
    if progress is not None and located is not None:
        progress["in_core_window"] = located[2]
    upcoming = _next_period(periods, stats, as_of)
    smoothing = max(1.0, 0.02 * peak_median) if math.isfinite(peak_median) else 1.0
    outlook = _outlook(sums, axis, as_of, other_years, smoothing)

    month = _month_to_date(sums, axis, value, as_of, other_years)
    bulletin = _bulletin(area_name, as_of, unit_short, level, rule, week, sources, progress, upcoming, outlook)
    if month["available"]:
        ratio = month.get("ratio_to_median")
        ratio_text = f", {ratio:.1f} times the median for the same days in other years" if ratio is not None else ""
        bulletin.insert(
            2 if week.get("available") else 1,
            f"{month['month_name']} to date ({month['days_elapsed']} days): "
            f"{_format_amount(month['value'], unit_short)}{ratio_text} (percentile {month['percentile']:.0f}).",
        )
    return {
        "as_of": as_of.isoformat(),
        "level": level,
        "level_label": LEVEL_LABELS[level] if level else None,
        "level_rule": rule,
        "level_scale": [{"key": k, "label": LEVEL_LABELS[k]} for k in LEVELS],
        "peak_week_median": nan_to_none(peak_median, 1),
        "last_7_days": week,
        "last_14_days": fortnight,
        "month_to_date": month,
        "recent_sources": sources,
        "reference_observed_recently": bool(np.any(recent_types == VT_REFERENCE)),
        "season_progress": progress,
        "critical_period_status": upcoming,
        "outlook": outlook,
        "bulletin": bulletin,
    }
