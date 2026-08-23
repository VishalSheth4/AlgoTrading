"""
Bidirectional engulfing + consolidation -- ported from
algoTrading/strategies/engulfing_consolidation.py.

Unlike strategies/engulfing_consolidation_sell.py (deliberately SELL-only
and H1-only, broadcast onto every chart), this is the original two-sided
pattern evaluated independently on EVERY timeframe like any other generic
strategy:

LONG  : bullish engulfing + 4-bar declining consolidation that breaks
        above the 7-bar-ago high.
SHORT : bearish engulfing + 4-bar rising consolidation that breaks below
        the 7-bar-ago low.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class EngulfingConsolidationStrategy(Strategy):
    name = "engulfing_consolidation"

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

        close, open_, high, low = df["close"], df["open"], df["high"], df["low"]
        close1, open1 = close.shift(1), open_.shift(1)

        bullish_engulfing = (close1 < open1) & (close > open_) & (open_ <= close1) & (close >= open1)
        bearish_engulfing = (close1 > open1) & (close < open_) & (open_ >= close1) & (close <= open1)

        long_consolidation = (
            (close < close1)
            & (close < close.shift(2))
            & (close < close.shift(3))
            & (close < close.shift(4))
            & (high > high.shift(7))
        )
        short_consolidation = (
            (close > close1)
            & (close > close.shift(2))
            & (close > close.shift(3))
            & (close > close.shift(4))
            & (low < low.shift(7))
        )

        df[f"{self.name}_bull"] = (bullish_engulfing & long_consolidation).fillna(False)
        df[f"{self.name}_bear"] = (bearish_engulfing & short_consolidation).fillna(False)
        df[f"{self.name}_bull_price"] = low
        df[f"{self.name}_bear_price"] = high

        # Per-signal SL/TP for auto-trading, mirroring
        # algoTrading/strategies/engulfing_consolidation.py exactly: SL =
        # the signal candle's own low/high, widened to include the
        # PREVIOUS candle's low/high if that previous candle was a
        # doji/small-body.
        prev_small_body = (
            ((high.shift(1) - low.shift(1)) > 0)
            & ((close1 - open1).abs() < 0.3 * (high.shift(1) - low.shift(1)))
        ).fillna(False)
        prev_low, prev_high = low.shift(1), high.shift(1)

        bull_sl = np.where(prev_small_body, np.minimum(low, prev_low), low)
        bear_sl = np.where(prev_small_body, np.maximum(high, prev_high), high)

        bull_risk = close - bull_sl
        bear_risk = bear_sl - close
        df[f"{self.name}_bull_sl"] = np.where(bull_risk > 0, bull_sl, np.nan)
        df[f"{self.name}_bull_tp"] = np.where(bull_risk > 0, close + self._rr * bull_risk, np.nan)
        df[f"{self.name}_bear_sl"] = np.where(bear_risk > 0, bear_sl, np.nan)
        df[f"{self.name}_bear_tp"] = np.where(bear_risk > 0, close - self._rr * bear_risk, np.nan)
        return df
