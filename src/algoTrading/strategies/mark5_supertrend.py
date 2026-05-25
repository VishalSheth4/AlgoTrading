import numpy as np
import pandas as pd
from algoTrading.config import Config
from algoTrading.strategies.SupertrendEngulfingReversalStrategy import Mark2Strategy


class Mark5SupertrendStrategy(Mark2Strategy):
    """
    Supertrend + 200 EMA trend filter.

    BUY  only when close > 200 EMA  (uptrend confirmed)
    SELL only when close < 200 EMA  (downtrend confirmed)

    Entry logic (after Supertrend flips):
      X candle = first candle of new trend (reference only)
      Watch next 2 candles for engulfing pattern:

      BUY TREND  + close > 200 EMA → bullish engulfing → LONG
        SL = min low from X..signal candle
        Invalidated if close drops below running low

      SELL TREND + close < 200 EMA → bearish engulfing → SHORT
        SL = max high from X..signal candle
        Invalidated if close breaks above running high

      One trade per trend segment.
    """

    _STRATEGY_KEY = "mark5_supertrend"

    def __init__(self, period=10, multiplier=3):
        super().__init__(period, multiplier)
        self.max_candle_size = getattr(Config, 'MAX_CANDLE_SIZE', None)

    def _candle_too_big(self, row) -> bool:
        if self.max_candle_size is None:
            return False
        return (row['high'] - row['low']) > self.max_candle_size

    def _has_big_lower_wick(self, row) -> bool:
        """True if lower wick is > 30% of the full high-low range."""
        full_range = row['high'] - row['low']
        if full_range <= 0:
            return False
        lower_wick = min(row['open'], row['close']) - row['low']
        return (lower_wick / full_range) > 0.30

    def _candle_strong(self, row) -> bool:
        """True if body is >= 50% of the full high-low range."""
        full_range = row['high'] - row['low']
        if full_range <= 0:
            return False
        body = abs(row['close'] - row['open'])
        return (body / full_range) >= 0.5

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df     = df.copy()
        df     = self.calculate_supertrend(df)
        ema200 = df['close'].ewm(span=200, adjust=False).mean()

        df['signal'] = 0
        df['sl']     = np.nan
        df['tp']     = np.nan
        df['lot']    = 0.0
        df['skip']   = ''

        trend  = df['st_trend'].values
        closes = df['close'].values
        highs  = df['high'].values
        lows   = df['low'].values
        n      = len(df)

        x_idx        = None
        active_trend = None
        done         = False
        running_ref  = None
        trend_count  = 0

        for i in range(200, n):

            # ── Supertrend flip ───────────────────────────────────────────
            if trend[i] != trend[i - 1]:
                prev_count  = trend_count
                trend_count = 1
                new_trend   = trend[i]

                if prev_count >= self.min_trend_candles:
                    # Edge case: on flip, if any of the 3 candles before X had a strong
                    # engulfing in the new trend direction, enter directly at X.
                    entered = False
                    if new_trend == 1:
                        for j in [i - 2, i - 1]:   # pairs: (i-3,i-2) and (i-2,i-1)
                            if j > 0:
                                p = df.iloc[j - 1]
                                c = df.iloc[j]
                                if (self.is_bullish_engulfing(p, c)
                                        and self._candle_strong(c)
                                        and not self._candle_too_big(c)
                                        and not self._has_big_lower_wick(c)):
                                    if closes[i] >= ema200.iloc[i]:
                                        entry      = closes[i]
                                        candle_buf = (highs[i] - lows[i]) * 0.05
                                        sl         = lows[i] - candle_buf
                                        risk       = entry - sl
                                        if risk > 0:
                                            df.at[i, 'signal'] = 1
                                            df.at[i, 'sl']     = sl
                                            df.at[i, 'tp']     = self.calc_tp(entry, risk, df.iloc[i]['st_upper'], 'long')
                                            df.at[i, 'lot']    = self.lot_size
                                        x_idx        = None
                                        active_trend = new_trend
                                        done         = True
                                        entered      = True
                                    break

                    elif new_trend == -1:
                        for j in [i - 2, i - 1]:   # pairs: (i-3,i-2) and (i-2,i-1)
                            if j > 0:
                                p = df.iloc[j - 1]
                                c = df.iloc[j]
                                if (self.is_bearish_engulfing(p, c)
                                        and self._candle_strong(c)
                                        and not self._candle_too_big(c)
                                        and not self._has_big_lower_wick(c)):
                                    if closes[i] <= ema200.iloc[i]:
                                        entry      = closes[i]
                                        candle_buf = (highs[i] - lows[i]) * 0.05
                                        sl         = highs[i] + candle_buf
                                        risk       = sl - entry
                                        if risk > 0:
                                            df.at[i, 'signal'] = -1
                                            df.at[i, 'sl']     = sl
                                            df.at[i, 'tp']     = self.calc_tp(entry, risk, df.iloc[i]['st_lower'], 'short')
                                            df.at[i, 'lot']    = self.lot_size
                                        x_idx        = None
                                        active_trend = new_trend
                                        done         = True
                                        entered      = True
                                    break

                    if not entered:
                        x_idx        = i
                        active_trend = new_trend
                        done         = False
                        # BUY trend: track running low; SELL trend: track running high
                        running_ref  = lows[i] if new_trend == 1 else highs[i]
                else:
                    x_idx        = None
                    active_trend = new_trend
                    done         = True
                continue

            trend_count += 1

            if x_idx is None or done:
                continue

            # Only watch first 2 candles after X
            if i - x_idx > 2:
                done = True
                continue

            prev_c = df.iloc[i - 1]
            curr_c = df.iloc[i]
            ema    = ema200.iloc[i]

            # ── BUY TREND + above 200 EMA → LONG on bullish engulfing ─────
            if active_trend == 1:
                if closes[i] < ema:
                    # EMA filter blocked — skip this segment
                    done = True
                elif closes[i] < running_ref:
                    # Price collapsed below support — invalidate
                    done = True
                elif (self.is_bullish_engulfing(prev_c, curr_c)
                        and not self._candle_too_big(curr_c)):
                    if self._has_big_lower_wick(curr_c):
                        df.at[i, 'skip'] = 'SKIP'
                        done = True
                    else:
                        entry      = closes[i]
                        candle_buf = (highs[i] - lows[i]) * 0.05
                        sl         = running_ref - candle_buf
                        risk       = entry - sl
                        if risk > 0:
                            df.at[i, 'signal'] = 1
                            df.at[i, 'sl']     = sl
                            df.at[i, 'tp']     = self.calc_tp(entry, risk, df.iloc[i]['st_upper'], 'long')
                            df.at[i, 'lot']    = self.lot_size
                        done = True
                else:
                    running_ref = min(running_ref, lows[i])

            # ── SELL TREND + below 200 EMA → SHORT on bearish engulfing ───
            elif active_trend == -1:
                if closes[i] > ema:
                    # EMA filter blocked — skip this segment
                    done = True
                elif closes[i] > running_ref:
                    # Price broke above resistance — invalidate
                    done = True
                elif (self.is_bearish_engulfing(prev_c, curr_c)
                        and not self._candle_too_big(curr_c)):
                    if self._has_big_lower_wick(curr_c):
                        df.at[i, 'skip'] = 'SKIP'
                        done = True
                    else:
                        entry      = closes[i]
                        candle_buf = (highs[i] - lows[i]) * 0.05
                        sl         = running_ref + candle_buf
                        risk       = sl - entry
                        if risk > 0:
                            df.at[i, 'signal'] = -1
                            df.at[i, 'sl']     = sl
                            df.at[i, 'tp']     = self.calc_tp(entry, risk, df.iloc[i]['st_lower'], 'short')
                            df.at[i, 'lot']    = self.lot_size
                        done = True
                else:
                    running_ref = max(running_ref, highs[i])

        return df
