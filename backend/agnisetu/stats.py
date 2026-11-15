"""Small, dependency free statistics used by the engines. NumPy only."""

from __future__ import annotations

import math

import numpy as np


def nan_to_none(value: float | None, digits: int | None = 4) -> float | None:
    if value is None:
        return None
    v = float(value)
    if not math.isfinite(v):
        return None
    return round(v, digits) if digits is not None else v


def to_list(values: np.ndarray, digits: int | None = 3) -> list[float | None]:
    """Convert an array to a JSON safe list, mapping NaN and infinities to None."""
    arr = np.asarray(values, dtype=float)
    if digits is not None:
        arr = np.round(arr, digits)
    out: list[float | None] = arr.tolist()
    if not np.all(np.isfinite(arr)):
        mask = ~np.isfinite(arr)
        flat = arr.ravel().tolist()
        flat_mask = mask.ravel().tolist()
        flat = [None if m else v for v, m in zip(flat, flat_mask, strict=True)]
        out = np.array(flat, dtype=object).reshape(arr.shape).tolist()
    return out


def quantiles(values: np.ndarray, probs: tuple[float, ...]) -> list[float]:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return [float("nan")] * len(probs)
    return [float(q) for q in np.quantile(arr, probs)]


def midrank_percentile(value: float, reference: np.ndarray) -> float:
    """Percentile of value within reference using the mid rank convention (0 to 100)."""
    ref = np.asarray(reference, dtype=float)
    ref = ref[np.isfinite(ref)]
    if ref.size == 0 or not math.isfinite(value):
        return float("nan")
    below = float(np.sum(ref < value))
    equal = float(np.sum(ref == value))
    return 100.0 * (below + 0.5 * equal) / ref.size


def robust_z(value: float, reference: np.ndarray) -> float:
    """(value - median) / (1.4826 MAD), falling back to the IQR when the MAD is zero."""
    ref = np.asarray(reference, dtype=float)
    ref = ref[np.isfinite(ref)]
    if ref.size < 3 or not math.isfinite(value):
        return float("nan")
    median = float(np.median(ref))
    mad = float(np.median(np.abs(ref - median)))
    scale = 1.4826 * mad
    if scale <= 0:
        q25, q75 = np.quantile(ref, [0.25, 0.75])
        scale = float(q75 - q25) / 1.349
    if scale <= 0:
        return float("nan")
    return (value - median) / scale


def normal_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def theil_sen(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Median of pairwise slopes and the matching intercept. Returns NaN with under 3 points."""
    xa = np.asarray(x, dtype=float)
    ya = np.asarray(y, dtype=float)
    ok = np.isfinite(xa) & np.isfinite(ya)
    xa, ya = xa[ok], ya[ok]
    if xa.size < 3:
        return float("nan"), float("nan")
    i, j = np.triu_indices(xa.size, k=1)
    dx = xa[j] - xa[i]
    keep = dx != 0
    if not np.any(keep):
        return float("nan"), float("nan")
    slope = float(np.median((ya[j] - ya[i])[keep] / dx[keep]))
    intercept = float(np.median(ya - slope * xa))
    return slope, intercept


def mann_kendall(y: np.ndarray) -> tuple[float, float, float]:
    """Mann-Kendall trend test for a series in time order.

    Returns (S, z, two sided p) using the normal approximation with tie correction and
    continuity correction. NaN when fewer than 4 finite values.
    """
    ya = np.asarray(y, dtype=float)
    ya = ya[np.isfinite(ya)]
    n = ya.size
    if n < 4:
        return float("nan"), float("nan"), float("nan")
    i, j = np.triu_indices(n, k=1)
    s = float(np.sum(np.sign(ya[j] - ya[i])))
    _, counts = np.unique(ya, return_counts=True)
    tie_term = float(np.sum(counts * (counts - 1) * (2 * counts + 5)))
    var = (n * (n - 1) * (2 * n + 5) - tie_term) / 18.0
    if var <= 0:
        return s, 0.0, 1.0
    if s > 0:
        z = (s - 1) / math.sqrt(var)
    elif s < 0:
        z = (s + 1) / math.sqrt(var)
    else:
        z = 0.0
    p = 2.0 * normal_sf(abs(z))
    return s, z, min(1.0, p)


def poisson_irls(
    design: np.ndarray,
    y: np.ndarray,
    ridge: float = 1e-2,
    max_iter: int = 60,
    tol: float = 1e-8,
) -> np.ndarray:
    """Quasi Poisson regression with a log link by iteratively reweighted least squares.

    The first column of the design matrix is treated as the intercept and is not penalized.
    Works for any non negative response, including non integer rates.
    """
    z = np.asarray(design, dtype=float)
    target = np.clip(np.asarray(y, dtype=float), 0.0, None)
    n, k = z.shape
    if n == 0:
        raise ValueError("No observations to fit")
    penalty = np.eye(k) * ridge
    penalty[0, 0] = 0.0
    mean = max(float(np.mean(target)), 1e-6)
    beta = np.zeros(k)
    beta[0] = math.log(mean)
    mu = np.full(n, mean)
    previous = np.inf
    for _ in range(max_iter):
        eta = np.clip(z @ beta, -30.0, 30.0)
        mu = np.exp(eta)
        working = eta + (target - mu) / mu
        weights = mu
        zw = z * weights[:, None]
        lhs = z.T @ zw + penalty
        rhs = zw.T @ working
        try:
            new_beta = np.linalg.solve(lhs, rhs)
        except np.linalg.LinAlgError:
            new_beta = np.linalg.lstsq(lhs, rhs, rcond=None)[0]
        # Step halving keeps the deviance from increasing on awkward data.
        step = 1.0
        for _ in range(8):
            candidate = beta + step * (new_beta - beta)
            mu_c = np.exp(np.clip(z @ candidate, -30.0, 30.0))
            with np.errstate(divide="ignore", invalid="ignore"):
                term = np.where(target > 0, target * np.log(target / mu_c), 0.0)
            deviance = 2.0 * float(np.sum(term - (target - mu_c))) + ridge * float(np.sum(candidate[1:] ** 2))
            if deviance <= previous + 1e-9 or step < 1e-2:
                break
            step /= 2.0
        converged = abs(previous - deviance) <= tol * (abs(deviance) + 1.0)
        beta = candidate
        previous = deviance
        if converged:
            break
    return beta


def circular_smooth(values: np.ndarray, kernel: tuple[float, ...] = (0.25, 0.5, 0.25)) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    half = len(kernel) // 2
    out = np.zeros_like(arr)
    for offset, weight in enumerate(kernel):
        out += weight * np.roll(arr, half - offset)
    return out


def rolling_median(values: np.ndarray, window: int) -> np.ndarray:
    """Centered rolling median with edge padding. Window must be odd."""
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return arr
    half = window // 2
    padded = np.pad(arr, half, mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, window)
    return np.median(view, axis=1)
