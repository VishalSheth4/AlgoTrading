import pandas as pd
import numpy as np
from algoTrading.config import Config


class SupertrendStrategy:

    def __init__(self, period=10, multiplier=3, rr=None):
        self.period     = period
        self.multiplier = multiplier
        self.rr         = Config.RR if rr is None else rr
        self.lot_size   = Config.LOT_SIZE

    # ── Supertrend (same correct implementation as Mark2) ─────────────
    def _supertrend(self, df):
        close = df['close'].values
        high  = df['high'].values
        low   = df['low'].values
        n     = len(df)

        hl2        = (high + low) / 2
        prev_close = np.concatenate([[close[0]], close[:-1]])

        tr = np.maximum.reduce([
            high - low,
            np.abs(high - prev_close),
            np.abs(low  - prev_close),
        ])

        # Wilder's RMA — matches TradingView ta.atr()
        atr = pd.Series(tr).ewm(
            alpha=1/self.period, min_periods=self.period, adjust=False
        ).mean().values

        basic_upper = hl2 + self.multiplier * atr
        basic_lower = hl2 - self.multiplier * atr

        final_upper = basic_upper.copy()
        final_lower = basic_lower.copy()

        for i in range(1, n):
            if np.isnan(basic_lower[i]):
                continue

            prev_lo = final_lower[i - 1]
            prev_hi = final_upper[i - 1]

            if np.isnan(prev_lo):
                final_lower[i] = basic_lower[i]
            elif basic_lower[i] > prev_lo or close[i - 1] < prev_lo:
                final_lower[i] = basic_lower[i]
            else:
                final_lower[i] = prev_lo

            if np.isnan(prev_hi):
                final_upper[i] = basic_upper[i]
            elif basic_upper[i] < prev_hi or close[i - 1] > prev_hi:
                final_upper[i] = basic_upper[i]
            else:
                final_upper[i] = prev_hi

        # State-based trend using CURRENT bar's adjusted bands.
        # TradingView starts direction=1 (bearish) during ATR warm-up → match that.
        #   trend ==  1  → bullish (green)
        #   trend == -1  → bearish (red)
        trend = -np.ones(n, dtype=int)   # start BEARISH — matches TV

        for i in range(1, n):
            lo = final_lower[i]
            hi = final_upper[i]

            if np.isnan(hi):
                trend[i] = trend[i - 1]
                continue

            if trend[i - 1] == 1:      # bullish → flip if close < lower
                trend[i] = -1 if close[i] < lo else 1
            else:                      # bearish → flip if close > upper
                trend[i] = 1  if close[i] > hi else -1

        return trend, final_lower, final_upper

    # ── Signal generation ─────────────────────────────────────────────
    def generate_signals(self, df):
        df = df.copy()

        trend, st_lower, st_upper = self._supertrend(df)

        df['st_trend'] = trend
        df['st_lower'] = st_lower
        df['st_upper'] = st_upper

        df['signal'] = 0
        df['sl']     = np.nan
        df['tp']     = np.nan
        df['lot']    = 0.0

        close = df['close'].values
        n     = len(df)

        for i in range(1, n):
            # LONG: bearish → bullish flip
            if trend[i] == 1 and trend[i - 1] == -1:
                entry = close[i]
                sl    = st_lower[i]
                risk  = entry - sl
                if risk > 0:
                    df.at[i, 'signal'] = 1
                    df.at[i, 'sl']     = sl
                    df.at[i, 'tp']     = entry + self.rr * risk
                    df.at[i, 'lot']    = self.lot_size

            # SHORT: bullish → bearish flip
            elif trend[i] == -1 and trend[i - 1] == 1:
                entry = close[i]
                sl    = st_upper[i]
                risk  = sl - entry
                if risk > 0:
                    df.at[i, 'signal'] = -1
                    df.at[i, 'sl']     = sl
                    df.at[i, 'tp']     = entry - self.rr * risk
                    df.at[i, 'lot']    = self.lot_size

        return df
