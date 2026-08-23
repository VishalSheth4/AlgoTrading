import pandas as pd
import numpy as np
from algoTrading.config import Config


class EngulfingStrategy:

    def __init__(self, rr=None):
        self.rr       = Config.RR if rr is None else rr
        self.lot_size = Config.LOT_SIZE

    def generate_signals(self, df):
        df = df.copy()

        df['signal'] = 0
        df['sl']     = np.nan
        df['tp']     = np.nan
        df['lot']    = 0.0

        for i in range(1, len(df)):
            curr = df.iloc[i]
            prev = df.iloc[i - 1]

            # ── Bullish Engulfing → LONG ──────────────────────────────
            if (
                prev['open'] > prev['close'] and                          # prev bearish
                curr['close'] > curr['open'] and                          # curr bullish
                curr['close'] >= prev['open'] and                         # engulfs top
                curr['open']  <= prev['close'] and                        # engulfs bottom
                (curr['close'] - curr['open']) > (prev['open'] - prev['close'])  # bigger body
            ):
                entry = curr['close']
                sl    = curr['low']
                risk  = entry - sl
                if risk > 0:
                    df.at[i, 'signal'] = 1
                    df.at[i, 'sl']     = sl
                    df.at[i, 'tp']     = entry + self.rr * risk
                    df.at[i, 'lot']    = self.lot_size

            # ── Bearish Engulfing → SHORT ─────────────────────────────
            elif (
                prev['close'] > prev['open'] and                          # prev bullish
                curr['open']  > curr['close'] and                         # curr bearish
                curr['open']  >= prev['close'] and                        # engulfs top
                curr['close'] <= prev['open'] and                         # engulfs bottom
                (curr['open'] - curr['close']) > (prev['close'] - prev['open'])  # bigger body
            ):
                entry = curr['close']
                sl    = curr['high']
                risk  = sl - entry
                if risk > 0:
                    df.at[i, 'signal'] = -1
                    df.at[i, 'sl']     = sl
                    df.at[i, 'tp']     = entry - self.rr * risk
                    df.at[i, 'lot']    = self.lot_size

        return df
