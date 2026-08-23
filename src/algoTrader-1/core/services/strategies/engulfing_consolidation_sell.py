"""
Bearish engulfing after a 4-candle down-consolidation that recently broke
a prior high -- SELL ONLY. Ported directly from the source Pine Script:

    engulfingFlag = (close[1] > open[1]) and (open > close) and
                     (open >= close[1]) and (open[1] >= close) and
                     (open - close > close[1] - open[1])

    consolidation = close < close[1] and close < close[2] and
                     close < close[3] and close < close[4] and
                     high > high[7]

    signal = consolidation and engulfingFlag

Deliberately evaluated ONLY on the H1 timeframe -- pattern shape is
timeframe-dependent, so CandleService applies this strategy manually to
the H1 DataFrame (never registered in the generic per-timeframe
StrategyRunner list) and then broadcasts the resulting SELL markers onto
every other timeframe's chart at the nearest matching bar.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class EngulfingConsolidationSellStrategy(Strategy):
    name = "engulfing_consolidation_sell"

    def __init__(self, rr: float = 1.0):
        # RR for auto-trading's own SL/TP (see {name}_sl/_tp below).
        self._rr = rr

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        close, open_, high, low = df["close"], df["open"], df["high"], df["low"]
        close1, open1 = close.shift(1), open_.shift(1)

        engulfing_flag = (
            (close1 > open1)
            & (open_ > close)
            & (open_ >= close1)
            & (open1 >= close)
            & ((open_ - close) > (close1 - open1))
        )

        consolidation = (
            (close < close1)
            & (close < close.shift(2))
            & (close < close.shift(3))
            & (close < close.shift(4))
            & (high > high.shift(7))
        )

        df[self.name] = (engulfing_flag & consolidation).fillna(False)
        df[f"{self.name}_price"] = high  # marker sits above the signal candle's high

        # Per-signal SL/TP for auto-trading, mirroring the SHORT side of
        # algoTrading/strategies/engulfing_consolidation.py: SL = the
        # signal candle's own high, widened to include the PREVIOUS
        # candle's high if that previous candle was a doji/small-body.
        prev_small_body = (
            ((high.shift(1) - low.shift(1)) > 0)
            & ((close1 - open1).abs() < 0.3 * (high.shift(1) - low.shift(1)))
        ).fillna(False)
        sl = np.where(prev_small_body, np.maximum(high, high.shift(1)), high)
        risk = sl - close
        df[f"{self.name}_sl"] = np.where(risk > 0, sl, np.nan)
        df[f"{self.name}_tp"] = np.where(risk > 0, close - self._rr * risk, np.nan)
        return df
