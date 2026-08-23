"""
Standard Wilder RSI (Relative Strength Index).

Not currently used to gate any strategy's signal -- computed as general
chart/filter infrastructure so a future strategy can read the `rsi`
column without adding its own indicator (OCP: one more Indicator in the
pipeline list, nothing else changes).
"""

from __future__ import annotations

import pandas as pd

from .base import Indicator


class RsiIndicator(Indicator):
    def __init__(self, period: int = 14):
        self._period = period

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.ewm(alpha=1 / self._period, min_periods=self._period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / self._period, min_periods=self._period, adjust=False).mean()

        rs = avg_gain / avg_loss.replace(0, float("nan"))
        rsi = 100 - (100 / (1 + rs))
        # 50 (neutral) during warm-up / when avg_loss is 0 -- never NaN,
        # since NaN breaks JSON.parse on the frontend (see engulfing.py).
        df["rsi"] = rsi.fillna(50.0)
        return df
