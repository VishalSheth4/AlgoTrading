"""
Bullish / bearish engulfing candlestick pattern detector.

- Bullish engulfing: previous candle is bearish, current candle is
  bullish, and the current body fully engulfs the previous body.
- Bearish engulfing: previous candle is bullish, current candle is
  bearish, and the current body fully engulfs the previous body.

Adds an "engulfing" column: "bullish" / "bearish" / "" per candle.

Uses "" rather than None as the no-pattern sentinel: pandas silently
normalizes None back to NaN in object columns under several operations
(e.g. .where()), and NaN serializes to the JSON token "NaN" -- which
Python's json module tolerates but browsers' JSON.parse rejects outright,
breaking the API response client-side. An empty string has no such
ambiguity and is still falsy for the frontend's equality checks.
"""

from __future__ import annotations

import pandas as pd

from .base import Indicator


class EngulfingPatternIndicator(Indicator):
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.reset_index(drop=True).copy()
        n = len(df)

        pattern: list[str] = [""] * n
        if n < 2:
            df["engulfing"] = pattern
            return df

        open_ = df["open"].to_numpy(dtype="float64")
        close_ = df["close"].to_numpy(dtype="float64")

        for i in range(1, n):
            prev_open, prev_close = open_[i - 1], close_[i - 1]
            curr_open, curr_close = open_[i], close_[i]

            prev_bearish = prev_close < prev_open
            prev_bullish = prev_close > prev_open
            curr_bullish = curr_close > curr_open
            curr_bearish = curr_close < curr_open

            if prev_bearish and curr_bullish and curr_open <= prev_close and curr_close >= prev_open:
                pattern[i] = "bullish"
            elif prev_bullish and curr_bearish and curr_open >= prev_close and curr_close <= prev_open:
                pattern[i] = "bearish"

        df["engulfing"] = pattern
        return df
