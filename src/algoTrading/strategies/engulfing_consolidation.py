import pandas as pd
import numpy as np
from algoTrading.config import Config


class EngulfingConsolidationStrategy:
    """
    Signal rules (one per bar, checked at candle close):

    LONG  — bullish engulfing  +  declining consolidation
      Engulfing : prev bearish, curr bullish, curr opens ≤ prev close,
                  curr closes ≥ prev open
      Consolidation: curr close < close[1,2,3,4]  (price drifting down)
                     AND curr high > high[7]       (breaks above 7-bar range)
      SL  : curr low  (extended to prev low  if prev is a doji)
      TP  : entry + RR × risk

    SHORT — bearish engulfing  +  rising consolidation
      Engulfing : prev bullish, curr bearish, curr opens ≥ prev close,
                  curr closes ≤ prev open
      Consolidation: curr close > close[1,2,3,4]  (price drifting up)
                     AND curr low < low[7]         (breaks below 7-bar range)
      SL  : curr high (extended to prev high if prev is a doji)
      TP  : entry − RR × risk
    """

    def __init__(self, rr=None):
        self.rr       = Config.RR if rr is None else rr
        self.lot_size = Config.LOT_SIZE

    # ── Candle pattern helpers ────────────────────────────────────────────

    def _is_small_body(self, candle) -> bool:
        """Doji / inside bar: body < 30 % of the full wick range."""
        body = abs(candle['close'] - candle['open'])
        rng  = candle['high'] - candle['low']
        return (body < 0.3 * rng) if rng > 0 else False

    def _is_bullish_engulfing(self, prev, curr) -> bool:
        return (
            prev['close'] < prev['open'] and       # prev candle bearish
            curr['close'] > curr['open'] and       # curr candle bullish
            curr['open']  <= prev['close'] and     # opens at/below prev close
            curr['close'] >= prev['open']          # closes at/above prev open
        )

    def _is_bearish_engulfing(self, prev, curr) -> bool:
        return (
            prev['close'] > prev['open'] and       # prev candle bullish
            curr['close'] < curr['open'] and       # curr candle bearish
            curr['open']  >= prev['close'] and     # opens at/above prev close
            curr['close'] <= prev['open']          # closes at/below prev open
        )

    # ── Consolidation helpers ─────────────────────────────────────────────

    def _long_consolidation(self, df: pd.DataFrame, i: int) -> bool:
        """
        Price has been drifting DOWN for 4 bars, then the current candle's
        high breaks above the 7-bar-ago high — exhaustion + momentum shift.
        """
        curr = df.iloc[i]
        return (
            curr['close'] < df.iloc[i - 1]['close'] and
            curr['close'] < df.iloc[i - 2]['close'] and
            curr['close'] < df.iloc[i - 3]['close'] and
            curr['close'] < df.iloc[i - 4]['close'] and
            curr['high']  > df.iloc[i - 7]['high']
        )

    def _short_consolidation(self, df: pd.DataFrame, i: int) -> bool:
        """
        Price has been drifting UP for 4 bars, then the current candle's
        low breaks below the 7-bar-ago low — exhaustion + momentum shift.
        """
        curr = df.iloc[i]
        return (
            curr['close'] > df.iloc[i - 1]['close'] and
            curr['close'] > df.iloc[i - 2]['close'] and
            curr['close'] > df.iloc[i - 3]['close'] and
            curr['close'] > df.iloc[i - 4]['close'] and
            curr['low']   < df.iloc[i - 7]['low']
        )

    # ── Signal generation ─────────────────────────────────────────────────

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['signal'] = 0
        df['sl']     = np.nan
        df['tp']     = np.nan
        df['lot']    = 0.0

        for i in range(8, len(df)):
            curr = df.iloc[i]
            prev = df.iloc[i - 1]

            # ── LONG: bullish engulfing after a declining consolidation ───
            if self._is_bullish_engulfing(prev, curr) and self._long_consolidation(df, i):
                entry = curr['close']
                # Extend SL to include the prev doji low if prev is a small body
                sl   = min(curr['low'], prev['low']) if self._is_small_body(prev) else curr['low']
                risk = entry - sl
                if risk > 0:
                    df.at[i, 'signal'] = 1
                    df.at[i, 'sl']     = sl
                    df.at[i, 'tp']     = entry + self.rr * risk
                    df.at[i, 'lot']    = self.lot_size

            # ── SHORT: bearish engulfing after a rising consolidation ─────
            elif self._is_bearish_engulfing(prev, curr) and self._short_consolidation(df, i):
                entry = curr['close']
                # Extend SL to include the prev doji high if prev is a small body
                sl   = max(curr['high'], prev['high']) if self._is_small_body(prev) else curr['high']
                risk = sl - entry
                if risk > 0:
                    df.at[i, 'signal'] = -1
                    df.at[i, 'sl']     = sl
                    df.at[i, 'tp']     = entry - self.rr * risk
                    df.at[i, 'lot']    = self.lot_size

        return df
