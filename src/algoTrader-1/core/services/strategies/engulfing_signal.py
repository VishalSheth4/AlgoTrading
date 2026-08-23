"""
Plain bullish/bearish engulfing signal -- ported from
algoTrading/strategies/engulfing_strategy.py.

Distinct from indicators/engulfing.py's EngulfingPatternIndicator: that
indicator only labels a candle "bullish"/"bearish" for chart coloring. This
strategy additionally requires the engulfing body to be strictly bigger
than the engulfed one, matching the original algoTrading signal (a stricter
condition than the indicator's plain overlap check), so this fires less
often and is meant to be tradeable, not just decorative.

Named "engulfing_signal" (not "engulfing") to avoid any confusion with the
indicator's "engulfing" column.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class EngulfingSignalStrategy(Strategy):
    name = "engulfing_signal"

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

        close, open_ = df["close"], df["open"]
        prev_close, prev_open = close.shift(1), open_.shift(1)

        bull = (
            (prev_open > prev_close)
            & (close > open_)
            & (close >= prev_open)
            & (open_ <= prev_close)
            & ((close - open_) > (prev_open - prev_close))
        ).fillna(False)

        bear = (
            (prev_close > prev_open)
            & (open_ > close)
            & (open_ >= prev_close)
            & (close <= prev_open)
            & ((open_ - close) > (prev_close - prev_open))
        ).fillna(False)

        df[f"{self.name}_bull"] = bull
        df[f"{self.name}_bear"] = bear
        df[f"{self.name}_bull_price"] = df["low"]
        df[f"{self.name}_bear_price"] = df["high"]

        # Per-signal SL/TP for auto-trading, mirroring
        # algoTrading/strategies/engulfing_strategy.py exactly: SL = the
        # signal candle's own low/high, TP = RR-based.
        low, high = df["low"], df["high"]
        bull_risk = close - low
        bear_risk = high - close
        df[f"{self.name}_bull_sl"] = np.where(bull_risk > 0, low, np.nan)
        df[f"{self.name}_bull_tp"] = np.where(bull_risk > 0, close + self._rr * bull_risk, np.nan)
        df[f"{self.name}_bear_sl"] = np.where(bear_risk > 0, high, np.nan)
        df[f"{self.name}_bear_tp"] = np.where(bear_risk > 0, close - self._rr * bear_risk, np.nan)
        return df
