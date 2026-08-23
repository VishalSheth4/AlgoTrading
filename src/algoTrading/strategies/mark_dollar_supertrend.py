import numpy as np
import pandas as pd
from algoTrading.config import Config
from algoTrading.strategies.mark2_strategy import Mark2Strategy


class MarkDollarSuperTrendStrategy(Mark2Strategy):
    """
    Combines Mark2 counter-trend logic with GreenDollar volume filter.

    Entry rules (evaluated in order on Supertrend flip):
      Rule 1 — Direct trend-following entry on X candle:
           flip → BUY  trend + X is bullish engulfing → LONG  (SL = X low)
           flip → SELL trend + X is bearish engulfing → SHORT (SL = X high)
      Rule 2 — Counter-trend setup via GreenDollar filter (if Rule 1 did not fire):
           flip → BUY  trend + X is bullish dollar candle → watch next 2 candles
                    bearish engulfing found → SHORT (SL = candle high)
           flip → SELL trend + X is bearish dollar candle → watch next 2 candles
                    bullish engulfing found → LONG  (SL = candle low)
      If neither rule matches → skip this trend segment entirely.
      TP at 1:RR from Config.RR (entry ± RR × risk).
      One trade per trend segment; price running away invalidates Rule 2 setup.
    """

    _STRATEGY_KEY = "mark_dollar_supertrend"

    def __init__(self, period=10, multiplier=3, rr=None):
        super().__init__(period, multiplier, rr)
        # self.rr and self.lot_size come from Mark2Strategy.__init__ via config.yaml
        self.max_candle_size = getattr(Config, 'MAX_CANDLE_SIZE', None)

    def _candle_too_big(self, row) -> bool:
        """True if the candle's high-low range exceeds MAX_CANDLE_SIZE."""
        if self.max_candle_size is None:
            return False
        return (row['high'] - row['low']) > self.max_candle_size

    # ── GreenDollar indicator columns ─────────────────────────────────

    def _prepare_dollar_flags(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['_ema5']          = df['close'].ewm(span=5).mean()
        df['_is_above_ema']  = (df['open'] > df['_ema5']) & (df['low'] > df['_ema5'])
        df['_not_above_ema'] = ~df['_is_above_ema']

        df['_avg_vol12']    = df['volume'].rolling(12).mean()
        df['_max_vol6']     = df['volume'].rolling(6).max().shift(1)
        df['_prev_low_vol'] = (
            (df['volume'].shift(1) < df['_avg_vol12']) &
            (df['volume'].shift(2) < df['_avg_vol12']) &
            (df['volume'].shift(3) < df['_avg_vol12']) &
            (df['volume'].shift(4) < df['_avg_vol12']) &
            (df['volume'].shift(5) < df['_avg_vol12'])
        )
        df['_vol_spike'] = (
            (df['volume'] > df['_max_vol6']) &
            (df['volume'] > df['_avg_vol12']) &
            df['_prev_low_vol']
        )
        return df

    def _is_dollar_long(self, row) -> bool:
        """Bullish GreenDollar: bullish body + at/below EMA + volume spike."""
        return bool(
            row['_vol_spike'] and
            row['close'] > row['open'] and
            row['_not_above_ema']
        )

    def _is_dollar_short(self, row) -> bool:
        """Bearish GreenDollar: bearish body + fully above EMA + volume spike."""
        return bool(
            row['_vol_spike'] and
            row['open'] > row['close'] and
            row['_is_above_ema']
        )

    # ── Signal generation ─────────────────────────────────────────────

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = self.calculate_supertrend(df)
        df = self._prepare_dollar_flags(df)

        df['signal']       = 0
        df['sl']           = np.nan
        df['tp']           = np.nan
        df['lot']          = 0.0
        df['reverse_exit'] = 0

        trend  = df['st_trend'].values
        closes = df['close'].values
        highs  = df['high'].values
        lows   = df['low'].values
        n      = len(df)

        x_idx        = None
        active_trend = None
        done         = False
        running_ref  = None

        for i in range(1, n):

            # ── Supertrend flipped ────────────────────────────────────────
            if trend[i] != trend[i - 1]:
                new_trend = trend[i]
                row_x     = df.iloc[i]
                prev_x    = df.iloc[i - 1]

                # Rule 1: X candle engulfs in the NEW trend direction → direct entry
                if (new_trend == 1 and self.is_bullish_engulfing(prev_x, row_x)
                        and not self._candle_too_big(row_x)):
                    entry = closes[i]
                    sl    = lows[i]
                    risk  = entry - sl
                    if risk > 0:
                        df.at[i, 'signal'] = 1
                        df.at[i, 'sl']     = sl
                        df.at[i, 'tp']     = self.calc_tp(entry, risk, np.nan, 'long')
                        df.at[i, 'lot']    = self.lot_size
                    x_idx        = None
                    active_trend = new_trend
                    done         = True

                elif (new_trend == -1 and self.is_bearish_engulfing(prev_x, row_x)
                        and not self._candle_too_big(row_x)):
                    entry = closes[i]
                    sl    = highs[i]
                    risk  = sl - entry
                    if risk > 0:
                        df.at[i, 'signal'] = -1
                        df.at[i, 'sl']     = sl
                        df.at[i, 'tp']     = self.calc_tp(entry, risk, np.nan, 'short')
                        df.at[i, 'lot']    = self.lot_size
                    x_idx        = None
                    active_trend = new_trend
                    done         = True

                # Rule 2: X candle is a GreenDollar signal → counter-trend watch
                elif new_trend == 1 and self._is_dollar_long(row_x):
                    x_idx        = i
                    active_trend = new_trend
                    done         = False
                    running_ref  = highs[i]
                elif new_trend == -1 and self._is_dollar_short(row_x):
                    x_idx        = i
                    active_trend = new_trend
                    done         = False
                    running_ref  = lows[i]

                else:
                    # Neither rule matched → skip this segment
                    x_idx        = None
                    active_trend = new_trend
                    done         = True
                continue

            if x_idx is None or done:
                continue

            # Only watch first 2 candles after X
            if i - x_idx > 2:
                done = True
                continue

            prev_c = df.iloc[i - 1]
            curr_c = df.iloc[i]

            # ── BUY TREND: bearish engulfing → SHORT ──────────────────────
            if active_trend == 1:
                if closes[i] > running_ref:
                    done = True
                elif self.is_bearish_engulfing(prev_c, curr_c) and not self._candle_too_big(curr_c):
                    entry = closes[i]
                    sl    = max(running_ref, highs[i])
                    risk  = sl - entry
                    if risk > 0:
                        df.at[i, 'signal'] = -1
                        df.at[i, 'sl']     = sl
                        df.at[i, 'tp']     = self.calc_tp(entry, risk, np.nan, 'short')
                        df.at[i, 'lot']    = self.lot_size
                    done = True
                else:
                    running_ref = max(running_ref, highs[i])

            # ── SELL TREND: bullish engulfing → LONG ──────────────────────
            elif active_trend == -1:
                if closes[i] < running_ref:
                    done = True
                elif self.is_bullish_engulfing(prev_c, curr_c) and not self._candle_too_big(curr_c):
                    entry = closes[i]
                    sl    = min(running_ref, lows[i])
                    risk  = entry - sl
                    if risk > 0:
                        df.at[i, 'signal'] = 1
                        df.at[i, 'sl']     = sl
                        df.at[i, 'tp']     = self.calc_tp(entry, risk, np.nan, 'long')
                        df.at[i, 'lot']    = self.lot_size
                    done = True
                else:
                    running_ref = min(running_ref, lows[i])

        # ── Second pass: mark reverse-exit candles ────────────────────
        # If the candle immediately after an entry reverse-engulfs the
        # entry candle, flag it so the engine exits early.
        sigs = df['signal'].values
        for i in range(1, n - 1):
            if sigs[i] == 0:
                continue
            entry_c = df.iloc[i]
            next_c  = df.iloc[i + 1]
            if sigs[i] == 1 and self.is_bearish_engulfing(entry_c, next_c):
                df.at[i + 1, 'reverse_exit'] = 1
            elif sigs[i] == -1 and self.is_bullish_engulfing(entry_c, next_c):
                df.at[i + 1, 'reverse_exit'] = 1

        return df
