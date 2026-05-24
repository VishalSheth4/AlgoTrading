import pandas as pd
import numpy as np
from algoTrading.strategies.mark2_strategy import _load_rr, _load_lot_size, _load_risk_per_trade


class GreenDollarStrategy:

    _STRATEGY_KEY = "green_dollar"

    def __init__(self):
        self.rr             = _load_rr(self._STRATEGY_KEY)
        self.lot_size       = _load_lot_size(self._STRATEGY_KEY)
        self.risk_per_trade = _load_risk_per_trade(self._STRATEGY_KEY)

    def is_small_body(self, candle):
        body = abs(candle['close'] - candle['open'])
        rng  = candle['high'] - candle['low']
        return body < (0.3 * rng) if rng > 0 else False

    def generate_signals(self, df):
        df = df.copy()

        df['ema']              = df['close'].ewm(span=5).mean()
        df['is_above_ema']     = (df['open'] > df['ema']) & (df['low'] > df['ema'])
        df['alert_not_above']  = ~df['is_above_ema']

        df['max_vol_6']  = df['volume'].rolling(6).max().shift(1)
        df['avg_vol_12'] = df['volume'].rolling(12).mean()

        df['prev_low_vol'] = (
            (df['volume'].shift(1) < df['avg_vol_12']) &
            (df['volume'].shift(2) < df['avg_vol_12']) &
            (df['volume'].shift(3) < df['avg_vol_12']) &
            (df['volume'].shift(4) < df['avg_vol_12']) &
            (df['volume'].shift(5) < df['avg_vol_12'])
        )

        df['signal'] = 0
        df['sl']     = np.nan
        df['tp']     = np.nan
        df['lot']    = 0.0

        for i in range(12, len(df)):
            curr = df.iloc[i]
            prev = df.iloc[i - 1]

            vol_spike = (
                curr['volume'] > curr['max_vol_6'] and
                curr['volume'] > curr['avg_vol_12'] and
                curr['prev_low_vol']
            )

            if not vol_spike:
                continue

            # ── LONG: bullish volume spike below EMA ─────────────────
            if curr['close'] > curr['open'] and curr['alert_not_above']:
                entry = curr['close']
                sl_candidates = [curr['low']]
                if self.is_small_body(prev):
                    sl_candidates.append(prev['low'])
                sl   = min(sl_candidates)
                risk = entry - sl
                if risk > 0 and risk >= 0.0001:
                    df.at[i, 'signal'] = 1
                    df.at[i, 'sl']     = sl
                    df.at[i, 'tp']     = entry + self.rr * risk
                    df.at[i, 'lot']    = self.lot_size

            # ── SHORT: bearish volume spike above EMA ─────────────────
            elif curr['open'] > curr['close'] and curr['is_above_ema']:
                entry = curr['close']
                sl_candidates = [curr['high']]
                if self.is_small_body(prev):
                    sl_candidates.append(prev['high'])
                sl   = max(sl_candidates)
                risk = sl - entry
                if risk > 0 and risk >= 0.0001:
                    df.at[i, 'signal'] = -1
                    df.at[i, 'sl']     = sl
                    df.at[i, 'tp']     = entry - self.rr * risk
                    df.at[i, 'lot']    = self.lot_size

        return df
