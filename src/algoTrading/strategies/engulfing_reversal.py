import pandas as pd
import numpy as np
from algoTrading.strategies.SupertrendEngulfingReversalStrategy import _load_rr, _load_lot_size, _load_risk_per_trade


class EngulfingReversalStrategy:

    _STRATEGY_KEY = "engulfing_reversal"

    def __init__(self):
        self.rr             = _load_rr(self._STRATEGY_KEY)
        self.lot_size       = _load_lot_size(self._STRATEGY_KEY)
        self.risk_per_trade = _load_risk_per_trade(self._STRATEGY_KEY)

    def is_bearish_engulfing(self, prev, curr):
        return (
            prev['close'] > prev['open'] and
            curr['open']  > curr['close'] and
            curr['open']  >= prev['close'] and
            curr['close'] <= prev['open']
        )

    def is_bullish_engulfing(self, prev, curr):
        return (
            prev['open']  > prev['close'] and
            curr['close'] > curr['open'] and
            curr['close'] >= prev['open'] and
            curr['open']  <= prev['close']
        )

    def is_small_body(self, candle):
        body  = abs(candle['close'] - candle['open'])
        rng   = candle['high'] - candle['low']
        return body < (0.3 * rng) if rng > 0 else False

    def generate_signals(self, df):
        df = df.copy()

        df['signal'] = 0
        df['sl']     = np.nan
        df['tp']     = np.nan
        df['lot']    = 0.0

        for i in range(3, len(df)):
            c1 = df.iloc[i - 3]   # anchor candle
            c2 = df.iloc[i - 2]   # first engulfing
            c3 = df.iloc[i - 1]   # second engulfing (entry close)
            c4 = df.iloc[i]       # confirmation / entry bar

            # ── LONG: bearish engulf → bullish engulf ─────────────────
            if self.is_bearish_engulfing(c1, c2) and self.is_bullish_engulfing(c2, c3):
                entry = c3['close']
                sl_candidates = [c2['low'], c3['low']]
                if self.is_small_body(c1):
                    sl_candidates.append(c1['low'])
                sl   = min(sl_candidates)
                risk = entry - sl
                if risk > 0:
                    df.at[i, 'signal'] = 1
                    df.at[i, 'sl']     = sl
                    df.at[i, 'tp']     = entry + self.rr * risk
                    df.at[i, 'lot']    = self.lot_size

            # ── SHORT: bullish engulf → bearish engulf ────────────────
            elif self.is_bullish_engulfing(c1, c2) and self.is_bearish_engulfing(c2, c3):
                entry = c3['close']
                sl_candidates = [c2['high'], c3['high']]
                if self.is_small_body(c1):
                    sl_candidates.append(c1['high'])
                sl   = max(sl_candidates)
                risk = sl - entry
                if risk > 0:
                    df.at[i, 'signal'] = -1
                    df.at[i, 'sl']     = sl
                    df.at[i, 'tp']     = entry - self.rr * risk
                    df.at[i, 'lot']    = self.lot_size

        return df
