"""
Supertrend(period, multiplier) indicator.

Adds two columns to the candle DataFrame:
- "supertrend": the indicator line value for that candle
- "trend": 1 for uptrend (price above the band), -1 for downtrend

Standard ATR-based Supertrend using Wilder-smoothed ATR, matching the
common TradingView default implementation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Indicator


class SupertrendIndicator(Indicator):
    def __init__(self, period: int = 10, multiplier: float = 3.0):
        self._period = period
        self._multiplier = multiplier

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.reset_index(drop=True).copy()
        n = len(df)
        period, mult = self._period, self._multiplier

        if n == 0:
            df["supertrend"] = pd.Series(dtype="float64")
            df["trend"] = pd.Series(dtype="int64")
            return df

        high = df["high"].to_numpy(dtype="float64")
        low = df["low"].to_numpy(dtype="float64")
        close = df["close"].to_numpy(dtype="float64")

        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]

        true_range = np.maximum.reduce([
            high - low,
            np.abs(high - prev_close),
            np.abs(low - prev_close),
        ])

        atr = np.zeros(n)
        warmup = min(period, n)
        atr[:warmup] = np.cumsum(true_range[:warmup]) / np.arange(1, warmup + 1)
        for i in range(period, n):
            atr[i] = (atr[i - 1] * (period - 1) + true_range[i]) / period

        hl2 = (high + low) / 2.0
        basic_upper = hl2 + mult * atr
        basic_lower = hl2 - mult * atr

        final_upper = np.zeros(n)
        final_lower = np.zeros(n)
        supertrend = np.zeros(n)
        trend = np.zeros(n, dtype="int64")

        final_upper[0] = basic_upper[0]
        final_lower[0] = basic_lower[0]
        supertrend[0] = final_upper[0]
        trend[0] = -1

        for i in range(1, n):
            final_upper[i] = (
                basic_upper[i]
                if (basic_upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1])
                else final_upper[i - 1]
            )
            final_lower[i] = (
                basic_lower[i]
                if (basic_lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1])
                else final_lower[i - 1]
            )

            if trend[i - 1] == 1:
                trend[i] = -1 if close[i] < final_lower[i] else 1
            else:
                trend[i] = 1 if close[i] > final_upper[i] else -1

            supertrend[i] = final_lower[i] if trend[i] == 1 else final_upper[i]

        df["supertrend"] = supertrend
        df["trend"] = trend
        return df
