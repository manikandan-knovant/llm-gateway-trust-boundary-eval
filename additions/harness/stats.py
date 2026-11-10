"""Statistics helpers for the baseline build 2026-11-09 harness. Standard library only.

- percentile: linear interpolation between closest ranks (the same method as numpy's default, "linear").
- wilson: Wilson score interval for a binomial proportion (95% by default).
- summarize: n, median, p95, p99, mean, min, max of a list of latencies.
- paired_median_difference: median of per-pair differences (b - a).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Z95 = 1.959963984540054  # two-sided 95% normal quantile


def percentile(values: Sequence[float], q: float) -> float:
    """q in [0, 100]. Linear interpolation between closest ranks (numpy 'linear')."""
    if not values:
        raise ValueError("percentile of an empty sequence")
    if not 0 <= q <= 100:
        raise ValueError("q must be between 0 and 100")
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(xs[lo])
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def median(values: Sequence[float]) -> float:
    return percentile(values, 50)


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval: (centre ± half-width) with

        centre     = (p + z²/2n) / (1 + z²/n)
        half-width = z · sqrt(p(1-p)/n + z²/4n²) / (1 + z²/n)

    Returns (lower, upper), clipped to [0, 1]. n = 0 gives (0, 1)."""
    if n < 0 or successes < 0 or successes > n:
        raise ValueError("need 0 <= successes <= n")
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    lo, hi = max(0.0, centre - half), min(1.0, centre + half)
    # at the extremes the exact bound is 0 (k = 0) or 1 (k = n); remove floating-point residue
    if successes == 0:
        lo = 0.0
    if successes == n:
        hi = 1.0
    return lo, hi


def summarize(values: Sequence[float]) -> dict[str, float]:
    if not values:
        raise ValueError("summary of an empty sequence")
    return {"n": len(values), "median": median(values), "p95": percentile(values, 95),
            "p99": percentile(values, 99), "mean": sum(values) / len(values),
            "min": float(min(values)), "max": float(max(values))}


def paired_median_difference(a: Sequence[float], b: Sequence[float]) -> float:
    """Median of b[i] - a[i] over pairs (equal lengths)."""
    if len(a) != len(b) or not a:
        raise ValueError("paired sequences must be non-empty and of equal length")
    return median([y - x for x, y in zip(a, b)])


def fmt_pct_ci(successes: int, n: int) -> str:
    lo, hi = wilson(successes, n)
    rate = successes / n if n else float("nan")
    return f"{successes}/{n} = {rate * 100:.1f}% [{lo * 100:.1f}, {hi * 100:.1f}]"
