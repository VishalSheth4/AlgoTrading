"""
Fractal swing high/low detection -- shared by multiple SMC concepts
(Order Blocks, Breaker Blocks, and future structure-based concepts), so
it lives here once rather than being reimplemented per concept.
"""

from __future__ import annotations

import numpy as np


def find_swing_points(high: np.ndarray, low: np.ndarray, length: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """
    Returns (swing_high, swing_low) boolean arrays.

    A swing high at index i is confirmed when high[i] is the strict
    maximum among the `length` bars on each side -- a local peak with
    `length` bars of right-side confirmation, same as a standard
    fractal/ZigZag indicator (meaning it's only knowable `length` bars
    after it happens, never on the bar itself). Swing low mirrors this
    for local troughs.
    """
    n = len(high)
    swing_high = np.zeros(n, dtype=bool)
    swing_low = np.zeros(n, dtype=bool)

    for i in range(length, n - length):
        window_high = high[i - length:i + length + 1]
        if high[i] == window_high.max() and np.argmax(window_high) == length:
            swing_high[i] = True

        window_low = low[i - length:i + length + 1]
        if low[i] == window_low.min() and np.argmin(window_low) == length:
            swing_low[i] = True

    return swing_high, swing_low
