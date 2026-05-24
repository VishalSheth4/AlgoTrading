import pandas as pd
import numpy as np
from algoTrading.strategies.mark2_strategy import _load_rr, _load_lot_size, _load_risk_per_trade

RSI_PERIOD = 14
RSI_OB     = 70   # overbought — BUY zone
RSI_OS     = 30   # oversold   — SELL zone


class RSIEngulfingStrategy:
    """
    RSI Engulfing — both directions.

    BUY  setup : RSI(14) > 70  on current bar AND bullish engulfing pattern
    SELL setup : RSI(14) < 30  on current bar AND bearish engulfing pattern

    Bullish engulfing:
      prev['close'] < prev['open']               prev candle bearish
      curr['close'] > curr['open']               curr candle bullish
      curr['open']  <= prev['close']             opens at/below prev close
      curr['close'] >= prev['open']              closes at/above prev open

    Bearish engulfing:
      prev['close'] > prev['open']               prev candle bullish
      curr['open']  > curr['close']              curr candle bearish
      curr['open']  >= prev['close']             opens at/above prev close
      curr['close'] <= prev['open']              closes at/below prev open

    Trade:
      BUY  — entry: curr close | SL: min(prev.low, curr.low)  | TP: entry + RR * risk
      SELL — entry: curr close | SL: max(prev.high, curr.high) | TP: entry - RR * risk
      Lot  : 0.01 (from config.yaml lot_size)
    """

    _STRATEGY_KEY = "rsi_engulfing"

    def __init__(self):
        self.rr             = _load_rr(self._STRATEGY_KEY)
        self.lot_size       = _load_lot_size(self._STRATEGY_KEY)
        self.risk_per_trade = _load_risk_per_trade(self._STRATEGY_KEY)

    # ── RSI (Wilder's RMA — matches Pine Script ta.rsi) ──────────────────────
    def _compute_rsi(self, close: pd.Series) -> pd.Series:
        delta    = close.diff()
        gain     = delta.clip(lower=0)
        loss     = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
        rs       = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _is_bullish_engulfing(self, prev, curr) -> bool:
        return (
            prev['close'] < prev['open'] and      # prev bearish
            curr['close'] > curr['open'] and      # curr bullish
            curr['open']  <= prev['close'] and    # opens at/below prev close
            curr['close'] >= prev['open']         # closes at/above prev open
        )

    def _is_bearish_engulfing(self, prev, curr) -> bool:
        return (
            prev['close'] > prev['open'] and      # prev bullish
            curr['open']  > curr['close'] and     # curr bearish
            curr['open']  >= prev['close'] and    # opens at/above prev close
            curr['close'] <= prev['open']         # closes at/below prev open
        )

    # ── Signal generation ─────────────────────────────────────────────────────
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['signal'] = 0
        df['sl']     = np.nan
        df['tp']     = np.nan
        df['lot']    = 0.0

        rsi = self._compute_rsi(df['close'])

        for i in range(RSI_PERIOD + 1, len(df)):
            rsi_val = rsi.iloc[i]
            if pd.isna(rsi_val):
                continue

            curr = df.iloc[i]
            prev = df.iloc[i - 1]

            # Skip oversized candles
            max_size = Config.MAX_CANDLE_SIZE
            if max_size is not None and (curr['high'] - curr['low']) > max_size:
                continue

            # ── BUY: RSI overbought + bullish engulfing ───────────────────────
            if rsi_val > RSI_OB and self._is_bullish_engulfing(prev, curr):
                entry = curr['close']
                sl    = min(prev['low'], curr['low'])
                risk  = entry - sl
                if risk > 0:
                    df.at[i, 'signal'] = 1
                    df.at[i, 'sl']     = sl
                    df.at[i, 'tp']     = entry + self.rr * risk
                    df.at[i, 'lot']    = self.lot_size

            # ── SELL: RSI oversold + bearish engulfing ────────────────────────
            elif rsi_val < RSI_OS and self._is_bearish_engulfing(prev, curr):
                entry = curr['close']
                sl    = max(prev['high'], curr['high'])
                risk  = sl - entry
                if risk > 0:
                    df.at[i, 'signal'] = -1
                    df.at[i, 'sl']     = sl
                    df.at[i, 'tp']     = entry - self.rr * risk
                    df.at[i, 'lot']    = self.lot_size

        return df
