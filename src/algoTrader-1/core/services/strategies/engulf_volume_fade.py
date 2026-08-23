"""
"Engulf Volume Fade" -- fades a weak, low-volume engulfing reversal that
immediately follows an opposite engulfing candle:

- SELL: candle A is a bearish engulfing candle, the very next candle B is
  a bullish engulfing candle (engulfing A's body), but B's volume is LESS
  than A's volume -- the "reversal" back up came on weaker participation
  than the move it's reversing, a sign it's unconvincing. Fade it: SELL,
  betting the original bearish move reasserts itself.
- BUY: the mirror image -- bullish engulfing candle A followed immediately
  by a lower-volume bearish engulfing candle B. Fade it: BUY.

Either direction: SL sits beyond candle B's own extreme (the low-volume
engulfing candle that triggered the fade), TP is RR-based off that risk.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class EngulfVolumeFadeStrategy(Strategy):
    name = "engulf_volume_fade"

    def __init__(self, rr: float = 1.0):
        # RR for auto-trading's own SL/TP (see {name}_bull_sl/_tp below).
        self._rr = rr

    @property
    def rr(self) -> float:
        return self._rr

    def set_rr(self, rr: float) -> None:
        self._rr = float(rr)

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

        open_ = df["open"]
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["tick_volume"]

        # Candle A = shift(1) (the one immediately before this row).
        # Candle "before A" = shift(2), needed to tell whether A itself
        # completed an engulfing pattern.
        a_open, a_close = open_.shift(1), close.shift(1)
        before_a_open, before_a_close = open_.shift(2), close.shift(2)

        # A is a bearish engulfing candle (engulfs the candle before it).
        a_is_bearish_engulfing = (
            (before_a_close > before_a_open)
            & (a_close < a_open)
            & (a_open >= before_a_close)
            & (a_close <= before_a_open)
        ).fillna(False)

        # A is a bullish engulfing candle (mirror).
        a_is_bullish_engulfing = (
            (before_a_close < before_a_open)
            & (a_close > a_open)
            & (a_open <= before_a_close)
            & (a_close >= before_a_open)
        ).fillna(False)

        # This row (B) is a bullish engulfing candle relative to A.
        b_is_bullish_engulfing = (
            (a_close < a_open)
            & (close > open_)
            & (open_ <= a_close)
            & (close >= a_open)
        ).fillna(False)

        # This row (B) is a bearish engulfing candle relative to A.
        b_is_bearish_engulfing = (
            (a_close > a_open)
            & (close < open_)
            & (open_ >= a_close)
            & (close <= a_open)
        ).fillna(False)

        b_volume_lighter_than_a = (volume < volume.shift(1)).fillna(False)

        # SELL: A bearish engulfing -> B bullish engulfing on lighter volume.
        bear_signal = (a_is_bearish_engulfing & b_is_bullish_engulfing & b_volume_lighter_than_a).fillna(False)
        # BUY: A bullish engulfing -> B bearish engulfing on lighter volume.
        bull_signal = (a_is_bullish_engulfing & b_is_bearish_engulfing & b_volume_lighter_than_a).fillna(False)

        df[f"{self.name}_bull"] = bull_signal
        df[f"{self.name}_bear"] = bear_signal
        df[f"{self.name}_bull_price"] = low
        df[f"{self.name}_bear_price"] = high

        # BUY: SL beyond B's own low, TP = RR-based off that risk.
        bull_risk = close - low
        df[f"{self.name}_bull_sl"] = np.where(bull_risk > 0, low, np.nan)
        df[f"{self.name}_bull_tp"] = np.where(bull_risk > 0, close + self._rr * bull_risk, np.nan)

        # SELL: SL beyond B's own high, TP = RR-based off that risk.
        bear_risk = high - close
        df[f"{self.name}_bear_sl"] = np.where(bear_risk > 0, high, np.nan)
        df[f"{self.name}_bear_tp"] = np.where(bear_risk > 0, close - self._rr * bear_risk, np.nan)
        return df
