"""
Simple moving-average crossover -- ported from
algoTrading/strategies/moving_average.py.

Golden cross (short MA crosses above long MA) -> bull signal.
Death cross (short MA crosses below long MA) -> bear signal.
"""

from __future__ import annotations

import pandas as pd

from .base import Strategy


class MovingAverageStrategy(Strategy):
    name = "moving_average"

    def __init__(self, short_window: int = 50, long_window: int = 200, rr: float = 1.0):
        self._short_window = short_window
        self._long_window = long_window
        # RR for auto-trading's own SL/TP (see {name}_bull_sl/_tp below).
        self._rr = rr

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.reset_index(drop=True).copy()
        n = len(df)

        if n == 0:
            df[f"{self.name}_bull"] = pd.Series(dtype="bool")
            df[f"{self.name}_bear"] = pd.Series(dtype="bool")
            df[f"{self.name}_bull_price"] = pd.Series(dtype="float64")
            df[f"{self.name}_bear_price"] = pd.Series(dtype="float64")
            df[f"{self.name}_bull_sl"] = pd.Series(dtype="float64")
            df[f"{self.name}_bull_tp"] = pd.Series(dtype="float64")
            df[f"{self.name}_bear_sl"] = pd.Series(dtype="float64")
            df[f"{self.name}_bear_tp"] = pd.Series(dtype="float64")
            return df

        close = df["close"]
        short_ma = close.rolling(self._short_window).mean()
        long_ma = close.rolling(self._long_window).mean()
        prev_short = short_ma.shift(1)
        prev_long = long_ma.shift(1)

        valid = short_ma.notna() & long_ma.notna() & prev_short.notna() & prev_long.notna()
        golden_cross = (prev_short <= prev_long) & (short_ma > long_ma)
        death_cross = (prev_short >= prev_long) & (short_ma < long_ma)

        df[f"{self.name}_bull"] = (golden_cross & valid).fillna(False)
        df[f"{self.name}_bear"] = (death_cross & valid).fillna(False)
        df[f"{self.name}_bull_price"] = df["low"]
        df[f"{self.name}_bear_price"] = df["high"]

        # Per-signal SL/TP for auto-trading, mirroring
        # algoTrading/strategies/moving_average.py exactly: SL = entry
        # -+ 2x ATR(14), TP = entry -+ RR x that same ATR-based risk.
        prev_close = close.shift(1)
        true_range = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr14 = true_range.rolling(14).mean()

        bull_risk = 2 * atr14
        bear_risk = 2 * atr14
        df[f"{self.name}_bull_sl"] = close - bull_risk
        df[f"{self.name}_bull_tp"] = close + self._rr * bull_risk
        df[f"{self.name}_bear_sl"] = close + bear_risk
        df[f"{self.name}_bear_tp"] = close - self._rr * bear_risk
        return df
