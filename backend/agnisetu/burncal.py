"""Burning activity calendar: weekly matrices, climatology, critical periods and season timing."""

from __future__ import annotations

import datetime as dt
from calendar import monthrange
from dataclasses import dataclass
from typing import Any

import numpy as np

from .stats import circular_smooth, nan_to_none, quantiles
from .timeaxis import N_BINS_PER_YEAR, TimeAxis, bin_dates, bin_label

MIN_COVERED_DAYS = 5
MIN_COMPLETE_BINS = 46
ACTIVITY_SHARE = 0.80
MIN_PERIOD_SHARE = 0.05
YEAR_ROUND_BINS = 40
WINDOW_PAD_BINS = 3
MIN_WINDOW_COVERAGE = 0.80
WEAK_YEAR_FRACTION = 0.20
ONSET_FRACTION = 0.10
END_FRACTION = 0.90
CLIM_PROBS = (0.10, 0.25, 0.50, 0.75, 0.90)

MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def daily_to_weekly(axis: TimeAxis, daily: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Activity per 7 days for every bin (NaN when fewer than 5 days are available) and day counts."""
    finite = np.isfinite(daily)
    days = np.bincount(axis.bin_id[finite], minlength=axis.n_bins)
    sums = np.bincount(axis.bin_id[finite], weights=daily[finite], minlength=axis.n_bins)
    with np.errstate(divide="ignore", invalid="ignore"):
        rate = np.where(days >= MIN_COVERED_DAYS, sums / days * 7.0, np.nan)
    return rate, days


def to_matrix(axis: TimeAxis, per_bin: np.ndarray) -> np.ndarray:
    return np.asarray(per_bin).reshape(axis.n_years, N_BINS_PER_YEAR)


def daily_matrix(axis: TimeAxis, daily: np.ndarray) -> np.ndarray:
    """Year by day of year matrix [n_years, 366]; NaN outside the record and on missing days."""
    out = np.full((axis.n_years, 366), np.nan)
    out[axis.year - axis.first_year, axis.doy - 1] = daily
    return out


def complete_years(matrix: np.ndarray) -> np.ndarray:
    return np.sum(np.isfinite(matrix), axis=1) >= MIN_COMPLETE_BINS


def annual_totals(axis: TimeAxis, daily: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Annual totals scaled for missing days, and the fraction of days available per year."""
    idx = axis.year - axis.first_year
    finite = np.isfinite(daily)
    have = np.bincount(idx[finite], minlength=axis.n_years).astype(float)
    sums = np.bincount(idx[finite], weights=daily[finite], minlength=axis.n_years)
    length = np.array([366.0 if _is_leap(y) else 365.0 for y in axis.years])
    fraction = have / length
    with np.errstate(divide="ignore", invalid="ignore"):
        totals = np.where(fraction >= 0.9, sums / have * length, np.nan)
    return totals, fraction


def month_bins(axis: TimeAxis) -> np.ndarray:
    """Index of the calendar month of every day: (year - first year) * 12 + month."""
    return (axis.year.astype(np.int64) - axis.first_year) * 12 + axis.month


def monthly_totals(axis: TimeAxis, daily: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Calendar month totals [year, 12], scaled for missing days.

    A month is reported when at least 80 percent of its calendar days have a value, and is NaN
    otherwise, so a month that is still in progress is never shown as a low month. Also returns
    the days with a value and the calendar length of every month.
    """
    idx = month_bins(axis)
    n = axis.n_years * 12
    finite = np.isfinite(daily)
    have = np.bincount(idx[finite], minlength=n).astype(float)
    sums = np.bincount(idx[finite], weights=daily[finite], minlength=n)
    length = np.array([monthrange(year, month)[1] for year in axis.years for month in range(1, 13)], dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        totals = np.where(have >= MIN_WINDOW_COVERAGE * length, sums / np.maximum(have, 1) * length, np.nan)
    shape = (axis.n_years, 12)
    return totals.reshape(shape), have.reshape(shape), length.reshape(shape)


def _is_leap(year: int) -> bool:
    return (year % 4 == 0 and year % 100 != 0) or year % 400 == 0


@dataclass(frozen=True)
class Climatology:
    years_used: list[int]
    mean: np.ndarray
    quantiles: np.ndarray  # [5, 52] for CLIM_PROBS
    minimum: np.ndarray
    maximum: np.ndarray
    n: np.ndarray

    @property
    def median(self) -> np.ndarray:
        return self.quantiles[2]

    def to_json(self) -> dict[str, Any]:
        def r(a: np.ndarray) -> list[float | None]:
            return [nan_to_none(v, 2) for v in a]

        return {
            "years_used": self.years_used,
            "mean": r(self.mean),
            "p10": r(self.quantiles[0]),
            "p25": r(self.quantiles[1]),
            "median": r(self.quantiles[2]),
            "p75": r(self.quantiles[3]),
            "p90": r(self.quantiles[4]),
            "min": r(self.minimum),
            "max": r(self.maximum),
            "n_years": [int(v) for v in self.n],
        }


def climatology(matrix: np.ndarray, years: list[int], baseline: tuple[int, int] | None = None) -> Climatology:
    use = complete_years(matrix)
    if baseline is not None:
        lo, hi = baseline
        use &= np.array([lo <= y <= hi for y in years])
    sub = matrix[use]
    used = [y for y, u in zip(years, use, strict=True) if u]
    if sub.shape[0] == 0:
        nan = np.full(N_BINS_PER_YEAR, np.nan)
        return Climatology([], nan, np.full((5, N_BINS_PER_YEAR), np.nan), nan, nan, np.zeros(N_BINS_PER_YEAR, int))
    with np.errstate(all="ignore"):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            mean = np.nanmean(sub, axis=0)
            q = np.nanquantile(sub, CLIM_PROBS, axis=0)
            minimum = np.nanmin(sub, axis=0)
            maximum = np.nanmax(sub, axis=0)
    return Climatology(used, mean, q, minimum, maximum, np.sum(np.isfinite(sub), axis=0))


@dataclass(frozen=True)
class CriticalPeriod:
    id: str
    name: str
    start_week: int
    end_week: int  # inclusive; smaller than start_week when the period wraps the new year
    weeks: tuple[int, ...]
    peak_week: int
    share: float

    @property
    def wraps(self) -> bool:
        return self.end_week < self.start_week

    def core_window(self, year: int) -> tuple[dt.date, dt.date]:
        start, _ = bin_dates(year, self.start_week)
        _, end = bin_dates(year + 1 if self.wraps else year, self.end_week)
        return start, end

    def extended_window(self, year: int) -> tuple[dt.date, dt.date]:
        start, end = self.core_window(year)
        pad = dt.timedelta(days=7 * WINDOW_PAD_BINS)
        return start - pad, end + pad


def _period_name(start_week: int, end_week: int) -> str:
    start, _ = bin_dates(2023, start_week)
    _, end = bin_dates(2023, end_week)
    if start.month == end.month:
        return f"{MONTHS[start.month - 1]} burning period"
    return f"{MONTHS[start.month - 1]} to {MONTHS[end.month - 1]} burning period"


def detect_critical_periods(clim_mean: np.ndarray) -> list[CriticalPeriod]:
    """Smallest set of weeks holding 80 percent of annual activity, grouped into contiguous periods."""
    mean = np.nan_to_num(np.asarray(clim_mean, dtype=float), nan=0.0)
    total = float(mean.sum())
    if total <= 0:
        return []
    smooth = circular_smooth(mean)
    order = np.argsort(-smooth, kind="stable")
    cumulative = np.cumsum(smooth[order]) / smooth.sum()
    n_pick = int(np.searchsorted(cumulative, ACTIVITY_SHARE) + 1)
    mark = np.zeros(N_BINS_PER_YEAR, dtype=bool)
    mark[order[:n_pick]] = True
    bridged = mark | (np.roll(mark, 1) & np.roll(mark, -1))
    # When the smallest 80 percent set needs nearly the whole year, burning has no season.
    if np.all(bridged) or n_pick >= YEAR_ROUND_BINS:
        peak = int(np.argmax(smooth))
        return [CriticalPeriod("p1", "Year round burning", 0, N_BINS_PER_YEAR - 1, tuple(range(52)), peak, 1.0)]

    # Walk the circle starting right after a gap so wrapped runs stay in one piece.
    first_gap = int(np.flatnonzero(~bridged)[0])
    runs: list[list[int]] = []
    current: list[int] = []
    for step in range(1, N_BINS_PER_YEAR + 1):
        week = (first_gap + step) % N_BINS_PER_YEAR
        if bridged[week]:
            current.append(week)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)

    shares = [float(mean[r].sum()) / total for r in runs]
    top = int(np.argmax(shares))
    kept = [(r, s) for i, (r, s) in enumerate(zip(runs, shares, strict=True)) if s >= MIN_PERIOD_SHARE or i == top]
    kept.sort(key=lambda item: item[0][0])
    periods = []
    for i, (run, share) in enumerate(kept, start=1):
        peak = int(run[int(np.argmax(smooth[run]))])
        periods.append(
            CriticalPeriod(
                f"p{i}",
                _period_name(run[0], run[-1]),
                int(run[0]),
                int(run[-1]),
                tuple(int(w) for w in run),
                peak,
                round(share, 4),
            )
        )
    return periods


@dataclass
class SeasonStats:
    """Per year totals and timing for one critical period."""

    years: list[int]
    total: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    estimated_share: np.ndarray  # fraction of the total that comes from estimated days
    coverage: np.ndarray
    onset: np.ndarray  # day offset from January 1 of the season year (NaN when undefined)
    peak: np.ndarray
    end: np.ndarray


def _window_slice(axis: TimeAxis, start: dt.date, end: dt.date) -> tuple[int, int, int]:
    lo = axis.index(start)
    hi = axis.index(end) + 1
    length = hi - lo
    return max(lo, 0), min(hi, axis.n_days), length


def season_stats(
    axis: TimeAxis,
    period: CriticalPeriod,
    value: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    is_estimate: np.ndarray,
) -> SeasonStats:
    years = axis.years
    n = len(years)
    total = np.full(n, np.nan)
    low = np.full(n, np.nan)
    high = np.full(n, np.nan)
    est = np.full(n, np.nan)
    cover = np.zeros(n)
    onset = np.full(n, np.nan)
    peak = np.full(n, np.nan)
    end = np.full(n, np.nan)

    for i, year in enumerate(years):
        start, stop = period.core_window(year)
        lo, hi, length = _window_slice(axis, start, stop)
        if hi <= lo:
            continue
        seg = value[lo:hi]
        finite = np.isfinite(seg)
        cover[i] = finite.sum() / length
        if cover[i] < MIN_WINDOW_COVERAGE:
            continue
        scale = length / finite.sum()
        total[i] = float(seg[finite].sum()) * scale
        low[i] = float(lower[lo:hi][finite].sum()) * scale
        high[i] = float(upper[lo:hi][finite].sum()) * scale
        estimated = float(seg[finite & is_estimate[lo:hi]].sum())
        est[i] = estimated / seg[finite].sum() if seg[finite].sum() > 0 else 0.0

    finite_totals = total[np.isfinite(total)]
    floor = WEAK_YEAR_FRACTION * float(np.median(finite_totals)) if finite_totals.size else 0.0
    for i, year in enumerate(years):
        if not np.isfinite(total[i]) or total[i] <= 0 or total[i] < floor:
            continue
        start, stop = period.extended_window(year)
        lo, hi, length = _window_slice(axis, start, stop)
        if hi <= lo:
            continue
        seg = value[lo:hi]
        finite = np.isfinite(seg)
        if finite.sum() / length < MIN_WINDOW_COVERAGE:
            continue
        filled = np.where(finite, seg, 0.0)
        cumulative = np.cumsum(filled)
        if cumulative[-1] <= 0:
            continue
        january_first = axis.index(dt.date(year, 1, 1))
        onset[i] = lo + int(np.searchsorted(cumulative, ONSET_FRACTION * cumulative[-1])) - january_first
        end[i] = lo + int(np.searchsorted(cumulative, END_FRACTION * cumulative[-1])) - january_first
        if filled.size >= 7:
            rolling = np.convolve(filled, np.ones(7), mode="valid")
            peak[i] = lo + int(np.argmax(rolling)) + 3 - january_first
        else:
            peak[i] = lo + int(np.argmax(filled)) - january_first
    return SeasonStats(years, total, low, high, est, cover, onset, peak, end)


def offset_to_label(offset: float | None) -> str | None:
    """Day offset from January 1 to a 'Mon D' label in a non leap reference year."""
    if offset is None or not np.isfinite(offset):
        return None
    day = dt.date(2023, 1, 1) + dt.timedelta(days=round(float(offset)))
    return f"{day:%b} {day.day}"


def period_summary(period: CriticalPeriod, stats: SeasonStats) -> dict[str, Any]:
    start, _ = bin_dates(2023, period.start_week)
    _, end = bin_dates(2024 if period.wraps else 2023, period.end_week)
    onset_q = quantiles(stats.onset, (0.25, 0.5, 0.75))
    peak_q = quantiles(stats.peak, (0.5,))
    end_q = quantiles(stats.end, (0.25, 0.5, 0.75))
    duration = stats.end - stats.onset + 1
    totals = stats.total[np.isfinite(stats.total)]
    earliest = float(np.nanmin(stats.onset)) if np.any(np.isfinite(stats.onset)) else None
    return {
        "id": period.id,
        "name": period.name,
        "start_week": period.start_week,
        "end_week": period.end_week,
        "weeks": list(period.weeks),
        "peak_week": period.peak_week,
        "wraps_year": period.wraps,
        "window_label": f"{start:%b} {start.day} to {end:%b} {end.day}",
        "peak_week_label": bin_label(period.peak_week),
        "share_of_annual": period.share,
        "median_total": nan_to_none(float(np.median(totals)) if totals.size else None, 1),
        "onset": {
            "median_offset": nan_to_none(onset_q[1], 1),
            "median": offset_to_label(onset_q[1]),
            "p25": offset_to_label(onset_q[0]),
            "p75": offset_to_label(onset_q[2]),
            "earliest": offset_to_label(earliest),
        },
        "peak": {"median_offset": nan_to_none(peak_q[0], 1), "median": offset_to_label(peak_q[0])},
        "end": {
            "median_offset": nan_to_none(end_q[1], 1),
            "median": offset_to_label(end_q[1]),
            "p25": offset_to_label(end_q[0]),
            "p75": offset_to_label(end_q[2]),
        },
        "median_duration_days": nan_to_none(quantiles(duration, (0.5,))[0], 0),
        "years_with_timing": int(np.sum(np.isfinite(stats.onset))),
    }
