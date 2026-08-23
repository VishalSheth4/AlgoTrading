"""
Two-step engulfing reversal -- ported from
algoTrading/strategies/engulfing_reversal.py.

LONG : a bearish engulfing candle (c2 engulfs c1) immediately followed by a
       bullish engulfing candle (c3 engulfs c2) -- signal fires on c3's
       confirmation bar, c4.
SHORT: the mirror image -- bullish engulfing then bearish engulfing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


def _bearish_engulfing(df: pd.DataFrame, prev_shift: int, curr_shift: int) -> pd.Series:
    prev_close = df["close"].shift(prev_shift)
    prev_open = df["open"].shift(prev_shift)
    curr_open = df["open"].shift(curr_shift)
    curr_close = df["close"].shift(curr_shift)
    return (prev_close > prev_open) & (curr_open > curr_close) & (curr_open >= prev_close) & (curr_close <= prev_open)


def _bullish_engulfing(df: pd.DataFrame, prev_shift: int, curr_shift: int) -> pd.Series:
    prev_close = df["close"].shift(prev_shift)
    prev_open = df["open"].shift(prev_shift)
    curr_open = df["open"].shift(curr_shift)
    curr_close = df["close"].shift(curr_shift)
    return (prev_open > prev_close) & (curr_close > curr_open) & (curr_close >= prev_open) & (curr_open <= prev_close)


class EngulfingReversalStrategy(Strategy):
    name = "engulfing_reversal"

    def __init__(self, rr: float = 1.0):
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

        # c1 = shift(3), c2 = shift(2), c3 = shift(1), c4 = current row.
        long_signal = _bearish_engulfing(df, 3, 2) & _bullish_engulfing(df, 2, 1)
        short_signal = _bullish_engulfing(df, 3, 2) & _bearish_engulfing(df, 2, 1)

        df[f"{self.name}_bull"] = long_signal.fillna(False)
        df[f"{self.name}_bear"] = short_signal.fillna(False)
        df[f"{self.name}_bull_price"] = df["low"]
        df[f"{self.name}_bear_price"] = df["high"]

        # Per-signal SL/TP for auto-trading, mirroring
        # algoTrading/strategies/engulfing_reversal.py exactly: SL = the
        # tighter of c2/c3's low (long) or high (short), widened to
        # include c1's low/high too if c1 was a doji/small-body.
        close = df["close"]
        c1_small_body = (
            ((df["high"].shift(3) - df["low"].shift(3)) > 0)
            & ((df["close"].shift(3) - df["open"].shift(3)).abs() < 0.3 * (df["high"].shift(3) - df["low"].shift(3)))
        ).fillna(False)
        c1_low, c2_low, c3_low = df["low"].shift(3), df["low"].shift(2), df["low"].shift(1)
        c1_high, c2_high, c3_high = df["high"].shift(3), df["high"].shift(2), df["high"].shift(1)

        bull_sl = np.minimum(c2_low, c3_low)
        bull_sl = np.where(c1_small_body, np.minimum(bull_sl, c1_low), bull_sl)
        bear_sl = np.maximum(c2_high, c3_high)
        bear_sl = np.where(c1_small_body, np.maximum(bear_sl, c1_high), bear_sl)

        bull_risk = close - bull_sl
        bear_risk = bear_sl - close
        df[f"{self.name}_bull_sl"] = np.where(bull_risk > 0, bull_sl, np.nan)
        df[f"{self.name}_bull_tp"] = np.where(bull_risk > 0, close + self._rr * bull_risk, np.nan)
        df[f"{self.name}_bear_sl"] = np.where(bear_risk > 0, bear_sl, np.nan)
        df[f"{self.name}_bear_tp"] = np.where(bear_risk > 0, close - self._rr * bear_risk, np.nan)
        return df
