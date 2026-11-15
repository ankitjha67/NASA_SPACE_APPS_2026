"""Daily time axis with the 52 bin calendar used across the application.

Bin w of a year holds days of year 7w + 1 to 7w + 7, except bin 51, which runs to the end of
the year (8 or 9 days). Weekly values are always expressed as activity per 7 days so that every
bin is comparable.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .store import EPOCH

N_BINS_PER_YEAR = 52


def week_of_doy(doy: np.ndarray | int) -> np.ndarray | int:
    return np.minimum((np.asarray(doy) - 1) // 7, N_BINS_PER_YEAR - 1)


def bin_start_doy(week: int) -> int:
    return 7 * week + 1


def bin_dates(year: int, week: int) -> tuple[dt.date, dt.date]:
    start = dt.date(year, 1, 1) + dt.timedelta(days=7 * week)
    end = dt.date(year, 12, 31) if week == N_BINS_PER_YEAR - 1 else start + dt.timedelta(days=6)
    return start, end


def bin_label(week: int) -> str:
    """Calendar label of a bin in a non leap year, for example 'Oct 29 to Nov 4'."""
    start, end = bin_dates(2023, week)
    return f"{start:%b} {start.day} to {end:%b} {end.day}"


@dataclass(frozen=True)
class TimeAxis:
    n_days: int
    first_year: int
    last_year: int
    year: np.ndarray  # int32 [day]
    doy: np.ndarray  # int32 [day], 1 based
    week: np.ndarray  # int32 [day], 0 to 51
    month: np.ndarray  # int32 [day], 0 to 11
    bin_id: np.ndarray  # int64 [day], (year - first_year) * 52 + week
    bin_len: np.ndarray  # int32 [bin], calendar days in the bin
    bin_week: np.ndarray  # int32 [bin]
    bin_year: np.ndarray  # int32 [bin]

    @property
    def n_years(self) -> int:
        return self.last_year - self.first_year + 1

    @property
    def n_bins(self) -> int:
        return self.n_years * N_BINS_PER_YEAR

    @property
    def years(self) -> list[int]:
        return list(range(self.first_year, self.last_year + 1))

    def date(self, index: int) -> dt.date:
        return EPOCH + dt.timedelta(days=int(index))

    def index(self, day: dt.date) -> int:
        return (day - EPOCH).days

    def iso_dates(self) -> list[str]:
        base = np.datetime64(EPOCH.isoformat(), "D")
        return np.datetime_as_string(base + np.arange(self.n_days), unit="D").tolist()


@lru_cache(maxsize=8)
def make_axis(n_days: int) -> TimeAxis:
    base = np.datetime64(EPOCH.isoformat(), "D")
    dates = base + np.arange(n_days)
    years = dates.astype("datetime64[Y]")
    year = years.astype(np.int32) + 1970
    doy = (dates - years.astype("datetime64[D]")).astype(np.int32) + 1
    week = np.minimum((doy - 1) // 7, N_BINS_PER_YEAR - 1).astype(np.int32)
    month = (dates.astype("datetime64[M]").astype(np.int64) % 12).astype(np.int32)
    first_year = int(year[0])
    last_year = int(year[-1])
    bin_id = (year.astype(np.int64) - first_year) * N_BINS_PER_YEAR + week
    n_years = last_year - first_year + 1
    bin_year = np.repeat(np.arange(first_year, last_year + 1, dtype=np.int32), N_BINS_PER_YEAR)
    bin_week = np.tile(np.arange(N_BINS_PER_YEAR, dtype=np.int32), n_years)
    bin_len = np.full(n_years * N_BINS_PER_YEAR, 7, dtype=np.int32)
    for i, y in enumerate(range(first_year, last_year + 1)):
        leap = (y % 4 == 0 and y % 100 != 0) or y % 400 == 0
        bin_len[i * N_BINS_PER_YEAR + N_BINS_PER_YEAR - 1] = 9 if leap else 8
    for arr in (year, doy, week, month, bin_id, bin_len, bin_week, bin_year):
        arr.setflags(write=False)
    return TimeAxis(n_days, first_year, last_year, year, doy, week, month, bin_id, bin_len, bin_week, bin_year)
