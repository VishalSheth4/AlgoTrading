"""
"Triple Wick Rejection" -- 3 consecutive candles that each show a
dominant wick (>=70% of that candle's own high-low range) on the SAME
side:

- 3 candles all with a bottom wick >= 70% of range -> repeated rejection
  of lower prices -> bullish signal, all 3 candles marked.
- 3 candles all with a top wick >= 70% of range -> repeated rejection of
  higher prices -> bearish signal, all 3 candles marked.

STRICT closed-candle rule: a window that would only complete using the
still-forming last candle is never marked, even partially -- its wick
ratio keeps changing tick to tick, so neither it nor the two candles
before it may be flagged until it actually closes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy

WICK_THRESHOLD = 0.70


class TripleWickRejectionStrategy(Strategy):
    name = "triple_wick_rejection"

    def __init__(self, rr: float = 1.0):
        # RR for auto-trading's own SL/TP (see {name}_bull_sl/_tp below).
        self._rr = rr

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.reset_index(drop=True).copy()
        n = len(df)

        if n == 0:
            df[f"{self.name}_bull"] = pd.Series(dtype="bool")
            df[f"{self.name}_bear"] = pd.Series(dtype="bool")
            df[f"{self.name}_bull_sl"] = pd.Series(dtype="float64")
            df[f"{self.name}_bull_tp"] = pd.Series(dtype="float64")
            df[f"{self.name}_bear_sl"] = pd.Series(dtype="float64")
            df[f"{self.name}_bear_tp"] = pd.Series(dtype="float64")
            return df

        candle_range = (df["high"] - df["low"]).replace(0, float("nan"))
        body_top = df[["open", "close"]].max(axis=1)
        body_bottom = df[["open", "close"]].min(axis=1)

        upper_wick_pct = (df["high"] - body_top) / candle_range
        lower_wick_pct = (body_bottom - df["low"]) / candle_range

        top_heavy = (upper_wick_pct >= WICK_THRESHOLD).fillna(False)
        bottom_heavy = (lower_wick_pct >= WICK_THRESHOLD).fillna(False)

        bull_window = bottom_heavy & bottom_heavy.shift(1).fillna(False) & bottom_heavy.shift(2).fillna(False)
        bear_window = top_heavy & top_heavy.shift(1).fillna(False) & top_heavy.shift(2).fillna(False)

        # A window ending on the still-forming last candle can never be a
        # confirmed pattern -- neither it nor the two candles before it
        # get marked from it.
        bull_window.iloc[-1] = False
        bear_window.iloc[-1] = False

        bull_flag = pd.Series(False, index=df.index)
        bear_flag = pd.Series(False, index=df.index)
        for i in df.index[bull_window]:
            bull_flag.loc[i - 2:i] = True
        for i in df.index[bear_window]:
            bear_flag.loc[i - 2:i] = True

        df[f"{self.name}_bull"] = bull_flag
        df[f"{self.name}_bear"] = bear_flag
        df[f"{self.name}_bull_price"] = df["low"]
        df[f"{self.name}_bear_price"] = df["high"]

        # Per-signal SL/TP for auto-trading. No backtest analog exists for
        # this strategy to mirror, so this is a deliberate, sensible
        # choice: SL beyond the 3-candle rejection zone's own extreme
        # (the low/high the wicks were repeatedly rejecting), TP = RR-based.
        close = df["close"]
        window_low = df["low"].rolling(3).min()
        window_high = df["high"].rolling(3).max()
        bull_risk = close - window_low
        bear_risk = window_high - close
        df[f"{self.name}_bull_sl"] = np.where(bull_risk > 0, window_low, np.nan)
        df[f"{self.name}_bull_tp"] = np.where(bull_risk > 0, close + self._rr * bull_risk, np.nan)
        df[f"{self.name}_bear_sl"] = np.where(bear_risk > 0, window_high, np.nan)
        df[f"{self.name}_bear_tp"] = np.where(bear_risk > 0, close - self._rr * bear_risk, np.nan)
        return df
