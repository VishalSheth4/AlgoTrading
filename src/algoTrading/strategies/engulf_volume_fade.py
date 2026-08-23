"""
"Engulf Volume Fade" -- fades a weak, low-volume engulfing reversal that
immediately follows an opposite engulfing candle.

Backtest port of algoTrader's core/services/strategies/engulf_volume_fade.py
(no shared code between the two packages -- see that file's module
docstring for the live version). Same rule, reimplemented in this
package's generate_signals(df) idiom:

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

from algoTrading.config import Config


class EngulfVolumeFadeStrategy:

    def __init__(self, rr=None):
        self.rr       = Config.RR if rr is None else rr
        self.lot_size = Config.LOT_SIZE

    def generate_signals(self, df):
        df = df.copy()

        df['signal'] = 0
        df['sl']     = np.nan
        df['tp']     = np.nan
        df['lot']    = 0.0

        if len(df) == 0:
            return df

        open_  = df['open']
        close  = df['close']
        high   = df['high']
        low    = df['low']
        volume = df['volume']

        a_open, a_close = open_.shift(1), close.shift(1)
        before_a_open, before_a_close = open_.shift(2), close.shift(2)

        a_is_bearish_engulfing = (
            (before_a_close > before_a_open)
            & (a_close < a_open)
            & (a_open >= before_a_close)
            & (a_close <= before_a_open)
        ).fillna(False)

        a_is_bullish_engulfing = (
            (before_a_close < before_a_open)
            & (a_close > a_open)
            & (a_open <= before_a_close)
            & (a_close >= before_a_open)
        ).fillna(False)

        b_is_bullish_engulfing = (
            (a_close < a_open)
            & (close > open_)
            & (open_ <= a_close)
            & (close >= a_open)
        ).fillna(False)

        b_is_bearish_engulfing = (
            (a_close > a_open)
            & (close < open_)
            & (open_ >= a_close)
            & (close <= a_open)
        ).fillna(False)

        b_volume_lighter_than_a = (volume < volume.shift(1)).fillna(False)

        bear_signal = (a_is_bearish_engulfing & b_is_bullish_engulfing & b_volume_lighter_than_a).fillna(False)
        bull_signal = (a_is_bullish_engulfing & b_is_bearish_engulfing & b_volume_lighter_than_a).fillna(False)

        entry = close

        # BUY: SL beyond B's own low.
        bull_risk = entry - low
        buy_valid = bull_signal & (bull_risk > 0)
        df.loc[buy_valid, 'signal'] = 1
        df.loc[buy_valid, 'sl']     = low[buy_valid]
        df.loc[buy_valid, 'tp']     = entry[buy_valid] + self.rr * bull_risk[buy_valid]
        df.loc[buy_valid, 'lot']    = self.lot_size

        # SELL: SL beyond B's own high.
        bear_risk = high - entry
        sell_valid = bear_signal & (bear_risk > 0)
        df.loc[sell_valid, 'signal'] = -1
        df.loc[sell_valid, 'sl']     = high[sell_valid]
        df.loc[sell_valid, 'tp']     = entry[sell_valid] - self.rr * bear_risk[sell_valid]
        df.loc[sell_valid, 'lot']    = self.lot_size

        return df
