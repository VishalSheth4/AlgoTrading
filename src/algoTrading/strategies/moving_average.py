import pandas as pd
import numpy as np
from algoTrading.strategies.SupertrendEngulfingReversalStrategy import _load_rr, _load_lot_size, _load_risk_per_trade


class MovingAverageStrategy:

    _STRATEGY_KEY = "moving_average"

    def __init__(self, short_window=50, long_window=200):
        self.short_window   = short_window
        self.long_window    = long_window
        self.rr             = _load_rr(self._STRATEGY_KEY)
        self.lot_size       = _load_lot_size(self._STRATEGY_KEY)
        self.risk_per_trade = _load_risk_per_trade(self._STRATEGY_KEY)

    def generate_signals(self, df):
        df = df.copy()

        df['short_ma'] = df['close'].rolling(self.short_window).mean()
        df['long_ma']  = df['close'].rolling(self.long_window).mean()

        # ATR (14-period) for dynamic SL
        prev_close = df['close'].shift(1)
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - prev_close).abs(),
            (df['low']  - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        df['signal'] = 0
        df['sl']     = np.nan
        df['tp']     = np.nan
        df['lot']    = 0.0

        for i in range(1, len(df)):
            prev_s = df['short_ma'].iloc[i - 1]
            curr_s = df['short_ma'].iloc[i]
            prev_l = df['long_ma'].iloc[i - 1]
            curr_l = df['long_ma'].iloc[i]

            if pd.isna(curr_s) or pd.isna(curr_l) or pd.isna(atr.iloc[i]):
                continue

            entry   = df['close'].iloc[i]
            atr_val = atr.iloc[i]

            # Golden cross → LONG
            if prev_s <= prev_l and curr_s > curr_l:
                sl   = entry - 2 * atr_val
                risk = entry - sl
                if risk > 0:
                    df.at[i, 'signal'] = 1
                    df.at[i, 'sl']     = sl
                    df.at[i, 'tp']     = entry + self.rr * risk
                    df.at[i, 'lot']    = self.lot_size

            # Death cross → SHORT
            elif prev_s >= prev_l and curr_s < curr_l:
                sl   = entry + 2 * atr_val
                risk = sl - entry
                if risk > 0:
                    df.at[i, 'signal'] = -1
                    df.at[i, 'sl']     = sl
                    df.at[i, 'tp']     = entry - self.rr * risk
                    df.at[i, 'lot']    = self.lot_size

        return df
