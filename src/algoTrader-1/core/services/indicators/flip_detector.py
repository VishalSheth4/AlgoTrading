"""
Detects a Supertrend direction flip on the most recently closed candle.

SRP: this class only compares trend values -- it knows nothing about
MT5, HTTP, or how the result is displayed.
"""

from __future__ import annotations

import pandas as pd


class TrendFlipDetector:
    def detect(self, df: pd.DataFrame) -> str | None:
        """Returns "bullish" / "bearish" if the last candle's trend differs
        from the one before it, else None."""
        if "trend" not in df.columns or len(df) < 2:
            return None

        prev_trend = df["trend"].iloc[-2]
        curr_trend = df["trend"].iloc[-1]

        if prev_trend == curr_trend:
            return None
        return "bullish" if curr_trend == 1 else "bearish"
