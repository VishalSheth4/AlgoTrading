"""
"20ema" -- engulfing rejection off a 20-period EMA, both directions.

Backtest port of algoTrader's core/services/strategies/ema20.py (no shared
code between the two packages -- see that file's module docstring for the
live version). Same rule, reimplemented in this package's
generate_signals(df) idiom instead of the live compute()/bull-bear-column
idiom:

- SELL: a bearish engulfing candle (previous candle bullish, current
  candle bearish, current body fully engulfs the previous body) whose
  own body crosses the EMA line. SL = that bearish candle's own high.
- BUY: the mirror image -- a bullish engulfing candle (previous candle
  bearish, current candle bullish, current body fully engulfs the
  previous body) whose own body crosses the EMA line. SL = that
  bullish candle's own low.
- Either direction: every one of the `lookback` candles immediately
  before the engulfing candle must sit entirely on one side of the EMA
  -- not even a wick may touch it -- so the cross is a clean,
  first-time break rather than the EMA already being chopped through.

Optional Supertrend confirmation (off by default, `supertrend_filter=True`
-- see the Settings UI's EMA20_SUPERTREND_FILTER field): when ON, BUY only
fires while the Supertrend is in an uptrend (green) and SELL only fires
while it's in a downtrend (red). Computes its own Supertrend internally
(same Wilder-ATR algorithm as algoTrader's SupertrendIndicator, so live
and backtest agree) since this package has no shared indicator pipeline.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from algoTrading.config import Config


def _compute_supertrend_direction(df, period: int = 10, multiplier: float = 3.0):
    """1 = uptrend (green), -1 = downtrend (red), one value per row.
    Same Wilder-ATR Supertrend algorithm as
    core/services/indicators/supertrend.py's SupertrendIndicator, kept as a
    self-contained copy here since algoTrading and algoTrader share no
    indicator code (see main_backtest.py's/ema20.py's module docstrings)."""
    n = len(df)
    if n == 0:
        return pd.Series(dtype="int64", index=df.index)

    high = df["high"].to_numpy(dtype="float64")
    low = df["low"].to_numpy(dtype="float64")
    close = df["close"].to_numpy(dtype="float64")

    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]

    true_range = np.maximum.reduce([
        high - low,
        np.abs(high - prev_close),
        np.abs(low - prev_close),
    ])

    atr = np.zeros(n)
    warmup = min(period, n)
    atr[:warmup] = np.cumsum(true_range[:warmup]) / np.arange(1, warmup + 1)
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + true_range[i]) / period

    hl2 = (high + low) / 2.0
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    final_upper = np.zeros(n)
    final_lower = np.zeros(n)
    trend = np.zeros(n, dtype="int64")

    final_upper[0] = basic_upper[0]
    final_lower[0] = basic_lower[0]
    trend[0] = -1

    for i in range(1, n):
        final_upper[i] = (
            basic_upper[i]
            if (basic_upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1])
            else final_upper[i - 1]
        )
        final_lower[i] = (
            basic_lower[i]
            if (basic_lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1])
            else final_lower[i - 1]
        )

        if trend[i - 1] == 1:
            trend[i] = -1 if close[i] < final_lower[i] else 1
        else:
            trend[i] = 1 if close[i] > final_upper[i] else -1

    return pd.Series(trend, index=df.index)


class Ema20Strategy:

    def __init__(self, rr=None, ema_period: int = 20, lookback: int = 3, supertrend_filter: bool = False):
        self.rr        = Config.RR if rr is None else rr
        self.lot_size  = Config.LOT_SIZE
        self.ema_period = ema_period
        # How many candles before the engulfing candle must stay clear of
        # the EMA, wicks included. Defaults to 3, tune via the constructor.
        self.lookback  = lookback
        # Off by default -- an opt-in filter, not a behavior change for an
        # existing backtest run. See Config.EMA20_SUPERTREND_FILTER.
        self.supertrend_filter = supertrend_filter

    def generate_signals(self, df):
        df = df.copy()

        df['signal'] = 0
        df['sl']     = np.nan
        df['tp']     = np.nan
        df['lot']    = 0.0

        if len(df) == 0:
            return df

        open_ = df['open']
        close = df['close']
        high  = df['high']
        low   = df['low']

        ema = close.ewm(span=self.ema_period, adjust=False).mean()

        prev_open  = open_.shift(1)
        prev_close = close.shift(1)

        # Bearish engulfing: previous candle bullish, current candle
        # bearish, current body fully engulfs the previous body.
        bearish_engulfing = (
            (prev_close > prev_open)
            & (close < open_)
            & (open_ >= prev_close)
            & (close <= prev_open)
        ).fillna(False)

        # Bullish engulfing: the mirror image.
        bullish_engulfing = (
            (prev_close < prev_open)
            & (close > open_)
            & (open_ <= prev_close)
            & (close >= prev_open)
        ).fillna(False)

        # Body crosses the EMA: the average sits between this candle's
        # open and close (order-independent, so it catches the cross
        # regardless of which side price started the candle on, and
        # works for both the bull and bear case below).
        body_top    = pd.concat([open_, close], axis=1).max(axis=1)
        body_bottom = pd.concat([open_, close], axis=1).min(axis=1)
        body_crosses_ema = (body_bottom <= ema) & (ema <= body_top)

        # Every one of the `lookback` candles right before the engulfing
        # candle must be entirely clear of the EMA -- high below it or
        # low above it, no wick touching -- so this is a clean first
        # break of the average, not one it was already chopping through.
        clear_of_ema = (high < ema) | (low > ema)
        prior_all_clear = pd.Series(True, index=df.index)
        for i in range(1, self.lookback + 1):
            prior_all_clear &= clear_of_ema.shift(i).fillna(False)

        bull_signal = (bullish_engulfing & body_crosses_ema & prior_all_clear).fillna(False)
        bear_signal = (bearish_engulfing & body_crosses_ema & prior_all_clear).fillna(False)

        if self.supertrend_filter:
            trend = _compute_supertrend_direction(df)
            bull_signal &= (trend == 1)
            bear_signal &= (trend == -1)

        entry = close

        # BUY: SL beyond the bullish engulfing candle's own low.
        bull_risk = entry - low
        buy_valid = bull_signal & (bull_risk > 0)
        df.loc[buy_valid, 'signal'] = 1
        df.loc[buy_valid, 'sl']     = low[buy_valid]
        df.loc[buy_valid, 'tp']     = entry[buy_valid] + self.rr * bull_risk[buy_valid]
        df.loc[buy_valid, 'lot']    = self.lot_size

        # SELL: SL beyond the bearish engulfing candle's own high.
        bear_risk = high - entry
        sell_valid = bear_signal & (bear_risk > 0)
        df.loc[sell_valid, 'signal'] = -1
        df.loc[sell_valid, 'sl']     = high[sell_valid]
        df.loc[sell_valid, 'tp']     = entry[sell_valid] - self.rr * bear_risk[sell_valid]
        df.loc[sell_valid, 'lot']    = self.lot_size

        return df
